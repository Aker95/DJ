"""Read-only Rekordbox inspection, reconciliation, and XML export."""

from __future__ import annotations

import re
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

from mutagen import File as MutagenFile

from .artifacts import now_iso, relative_to_root, seal_manifest
from .audio import AUDIO_EXTENSIONS
from .config import Settings
from .matching import match_scores
from .schema import (
    bpm_in_master,
    bpm_master,
    canonical_key,
    iter_destinations,
    master_folder_index,
    resolve_destination,
    validate_schema,
)

_KIND_BY_EXTENSION = {
    ".aac": "AAC File",
    ".aif": "AIFF File",
    ".aiff": "AIFF File",
    ".alac": "ALAC File",
    ".flac": "FLAC File",
    ".m4a": "M4A File",
    ".mp3": "MP3 File",
    ".ogg": "OGG File",
    ".opus": "Opus File",
    ".wav": "WAV File",
}


def normalize_bpm(raw_bpm: float | int | None) -> tuple[float | None, bool]:
    if raw_bpm is None or float(raw_bpm) <= 0:
        return None, False
    bpm = float(raw_bpm)
    doubled = bpm < 118 and 118 <= bpm * 2 <= 180
    return round(bpm * 2 if doubled else bpm, 4), doubled


def _snapshot_database(source: Path, destination: Path) -> None:  # pragma: no cover
    shutil.copy2(source, destination)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, Path(str(destination) + suffix))


def read_tracks(  # pragma: no cover
    database_path: Path, schema: dict, *, settings: Settings
) -> list[dict]:
    """Query only a temporary snapshot of the live Rekordbox database."""

    try:
        import pyrekordbox.db6.database as database_module
        from pyrekordbox import Rekordbox6Database
    except ImportError as error:
        raise RuntimeError("Install the 'rekordbox' project extra to inspect Rekordbox") from error
    destination_by_parent = {
        Path(item["local_folder"]).resolve(): item
        for item in iter_destinations(schema, project_root=settings.project_root)
        if item.get("local_folder")
    }
    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="dj-rekordbox-readonly-") as temporary:
        snapshot = Path(temporary) / "master.db"
        _snapshot_database(database_path, snapshot)
        database_module.get_rekordbox_pid = lambda: None
        database = Rekordbox6Database(path=snapshot)
        try:
            for content in database.get_content():
                path = Path(str(content.FolderPath or "")).resolve()
                try:
                    path.relative_to(settings.music_root)
                except (OSError, ValueError):
                    continue
                if not path.is_file():
                    continue
                destination = destination_by_parent.get(path.parent)
                try:
                    artist = content.Artist.Name if content.Artist else ""
                except Exception:
                    artist = ""
                bpm, doubled = normalize_bpm(float(content.BPM) / 100 if content.BPM else None)
                rows.append(
                    {
                        "rekordbox_content_id": str(content.ID),
                        "path": relative_to_root(path, settings),
                        "artist": artist,
                        "title": content.Title or path.stem,
                        "duration_ms": int(content.Length or 0) * 1000 or None,
                        "isrc": (content.ISRC or "").strip().upper() or None,
                        "rekordbox_bpm": bpm,
                        "half_time_normalized": doubled,
                        "current_destination": (
                            {
                                "master_folder": destination["master_folder"],
                                "main_genre": destination["main_genre"],
                                "subgenre": destination["subgenre"],
                                "placement_id": destination["placement_id"],
                            }
                            if destination
                            else None
                        ),
                    }
                )
        finally:
            database.close()
    return rows


def verify_structure(
    tracks: list[dict],
    schema: dict,
    *,
    registry: dict | None = None,
) -> dict:
    """Audit current paths against existing Rekordbox BPM without reanalysis."""

    masters = master_folder_index(schema)
    destinations = {item["placement_id"]: item for item in iter_destinations(schema)}
    registry_by_path = {
        item.get("local_path"): {"spotify_id": spotify_id, **item}
        for spotify_id, item in (registry or {}).get("tracks", {}).items()
        if item.get("local_path")
    }
    records: list[dict] = []
    review: list[dict] = []
    for track in tracks:
        current = track.get("current_destination")
        flags: list[str] = []
        if not current:
            flags.append("schema-path-missing")
            current_destination = None
        else:
            current_destination = destinations.get(current.get("placement_id"))
            current_master = masters.get(canonical_key(current.get("master_folder") or ""))
            bpm = track.get("rekordbox_bpm")
            if bpm is None:
                flags.append("rekordbox-bpm-missing")
            elif not current_master or not bpm_in_master(current_master, bpm):
                flags.append("bpm-band-mismatch")
        bpm = track.get("rekordbox_bpm")
        suggested_master = bpm_master(schema, bpm)
        suggested_destination = None
        if suggested_master and current:
            suggested_destination = resolve_destination(
                schema,
                master_folder=suggested_master["name"],
                main_genre=current["main_genre"],
                subgenre=current.get("subgenre"),
            )
            if "bpm-band-mismatch" in flags and suggested_destination is None:
                flags.append("same-taxonomy-placement-missing")
        item = {
            **track,
            "spotify": registry_by_path.get(track["path"]),
            "current_destination": current_destination or current,
            "suggested_master_folder": suggested_master["name"] if suggested_master else None,
            "suggested_destination": suggested_destination,
            "review_flags": flags,
            "status": "review_required" if flags else "consistent",
        }
        records.append(item)
        if flags:
            review.append(item)
    return seal_manifest(
        {
            "artifact_type": "existing_structure_verification",
            "artifact_version": 2,
            "created_at": now_iso(),
            "tracks": records,
            "review_tracks": review,
            "summary": {
                "files_checked": len(records),
                "consistent": len(records) - len(review),
                "review_required": len(review),
            },
        }
    )


def fuzzy_score(local: dict, spotify_track: dict) -> tuple[float, dict] | None:
    local_isrc = local.get("isrc")
    spotify_isrc = (spotify_track.get("isrc") or "").strip().upper() or None
    if local_isrc and spotify_isrc and local_isrc != spotify_isrc:
        return None
    local_title = local.get("title", "").casefold()
    spotify_title = spotify_track.get("title", "").casefold()
    for token in ("remix", "radio"):
        if bool(re.search(rf"\b{token}\b", local_title)) != bool(
            re.search(rf"\b{token}\b", spotify_title)
        ):
            return None
    artist_score, title_score = match_scores(
        local.get("artist", ""),
        local.get("title", ""),
        spotify_track.get("artist", ""),
        spotify_track.get("title", ""),
    )
    duration_score = 1.0
    difference = None
    if local.get("duration_ms") and spotify_track.get("duration_ms"):
        difference = abs(local["duration_ms"] - spotify_track["duration_ms"])
        duration_score = max(0, 1 - difference / 15000)
    if artist_score < 0.80 or title_score < 0.85 or duration_score < 0.5:
        return None
    score = artist_score * 0.4 + title_score * 0.5 + duration_score * 0.1
    return score, {
        "artist": round(artist_score, 4),
        "title": round(title_score, 4),
        "duration": round(duration_score, 4),
        "duration_difference_ms": difference,
    }


def match_spotify_track(local: dict, reviewed_tracks: list[dict]) -> dict:
    by_isrc: dict[str, list[dict]] = defaultdict(list)
    for track in reviewed_tracks:
        if track.get("isrc"):
            by_isrc[track["isrc"].strip().upper()].append(track)
    exact = by_isrc.get(local.get("isrc"), []) if local.get("isrc") else []
    if len(exact) == 1:
        return {"status": "verified", "method": "isrc", "score": 1.0, "track": exact[0]}
    if len(exact) > 1:
        return {"status": "identity_review", "reason": "non_unique_isrc"}
    candidates: list[tuple[float, dict, dict]] = []
    for track in reviewed_tracks:
        scored = fuzzy_score(local, track)
        if scored:
            candidates.append((scored[0], track, scored[1]))
    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates:
        return {"status": "unmatched", "reason": "no_safe_identity_match"}
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.03:
        return {
            "status": "identity_review",
            "reason": "ambiguous_fuzzy_match",
            "candidate_track_ids": [item[1]["spotify_track_id"] for item in candidates[:3]],
        }
    score, track, components = candidates[0]
    return {
        "status": "verified",
        "method": "metadata_duration",
        "score": round(score, 4),
        "score_components": components,
        "track": track,
    }


def reconcile(schema: dict, reviewed: dict, local_tracks: list[dict]) -> dict:
    """Compare confirmed Spotify classifications with existing Rekordbox BPM."""

    validate_schema(schema)
    reviewed_tracks = reviewed.get("tracks", [])
    masters = master_folder_index(schema)
    records: list[dict] = []
    for local in local_tracks:
        identity = match_spotify_track(local, reviewed_tracks)
        base = {
            **local,
            "identity": {key: value for key, value in identity.items() if key != "track"},
        }
        if identity["status"] != "verified":
            records.append({**base, "status": identity["status"]})
            continue
        track = identity["track"]
        master = bpm_master(schema, local.get("rekordbox_bpm"))
        spotify_destination = {
            "master_folder": track["master_folder"],
            "main_genre": track["main_genre"],
            "subgenre": track.get("subgenre"),
            "target_playlist_id": track.get("target_playlist_id"),
        }
        if master is None:
            records.append(
                {
                    **base,
                    "status": "bpm_review",
                    "spotify_track_id": track["spotify_track_id"],
                    "spotify_destination": spotify_destination,
                }
            )
            continue
        expected_master = masters.get(canonical_key(track["master_folder"] or ""))
        if expected_master and expected_master["id"] == master["id"]:
            records.append(
                {
                    **base,
                    "status": "verified_same_band",
                    "spotify_track_id": track["spotify_track_id"],
                    "spotify_destination": spotify_destination,
                    "rekordbox_master_folder": master["name"],
                }
            )
            continue
        destination = resolve_destination(
            schema,
            master_folder=master["name"],
            main_genre=track["main_genre"],
            subgenre=track.get("subgenre"),
        )
        records.append(
            {
                **base,
                "status": "cross_band_review",
                "spotify_track_id": track["spotify_track_id"],
                "spotify_destination": spotify_destination,
                "rekordbox_master_folder": master["name"],
                "proposed_route": (
                    {
                        "status": "route_exists",
                        "placement_id": destination["placement_id"],
                        "local_folder": destination["local_folder"],
                        "spotify_playlist_id": destination.get("spotify_playlist"),
                    }
                    if destination
                    else {
                        "status": "schema_topology_required",
                        "master_folder": master["name"],
                        "main_genre": track["main_genre"],
                        "subgenre": track.get("subgenre"),
                    }
                ),
            }
        )
    counts = Counter(record["status"] for record in records)
    return {
        "artifact_type": "rekordbox_spotify_reconciliation",
        "artifact_version": 2,
        "created_at": now_iso(),
        "records": records,
        "review_records": [
            record for record in records if record["status"] != "verified_same_band"
        ],
        "summary": {"rekordbox_files": len(local_tracks), **dict(counts)},
    }


def _first_tag(tags: Any, *keys: str) -> str | None:
    for key in keys:
        try:
            value = tags.get(key) if tags else None
        except (AttributeError, TypeError):
            continue
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _xml_metadata(path: Path) -> dict[str, str]:
    metadata = {
        "Name": path.stem,
        "Kind": _KIND_BY_EXTENSION.get(path.suffix.casefold(), "Audio File"),
        "Size": str(path.stat().st_size),
        "DateModified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
    }
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        return metadata
    if audio is None:
        return metadata
    for output, keys in (
        ("Name", ("title",)),
        ("Artist", ("artist", "albumartist")),
        ("Album", ("album",)),
    ):
        if value := _first_tag(audio.tags, *keys):
            metadata[output] = value
    info = getattr(audio, "info", None)
    for output, attribute, divisor in (
        ("TotalTime", "length", 1),
        ("BitRate", "bitrate", 1000),
        ("SampleRate", "sample_rate", 1),
    ):
        if value := getattr(info, attribute, None):
            metadata[output] = str(round(float(value) / divisor))
    return metadata


def _playlist_node(name: str, track_ids: list[int]) -> ET.Element:
    node = ET.Element(
        "NODE", {"Type": "1", "Name": name, "Entries": str(len(track_ids)), "KeyType": "0"}
    )
    for track_id in track_ids:
        ET.SubElement(node, "TRACK", {"Key": str(track_id)})
    return node


def export_xml(schema: dict, *, settings: Settings, output_path: Path) -> dict:
    """Export only direct files from final schema destinations."""

    genres = {genre["id"]: genre for genre in schema.get("main_genres", [])}
    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    ET.SubElement(root, "PRODUCT", {"Name": "DJ Library", "Version": "1.0", "Company": "Personal"})
    collection = ET.SubElement(root, "COLLECTION", {"Entries": "0"})
    playlists = ET.SubElement(root, "PLAYLISTS")
    masters = schema.get("master_folders", [])
    playlist_root = ET.SubElement(
        playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": str(len(masters))}
    )
    ids: dict[Path, int] = {}
    missing = 0
    playlist_count = 0

    def register(folder_value: str | None) -> list[int]:
        nonlocal missing
        if not folder_value:
            return []
        folder = (settings.project_root / folder_value).resolve()
        if not folder.is_dir():
            missing += 1
            return []
        results: list[int] = []
        for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_file() or path.suffix.casefold() not in AUDIO_EXTENSIONS:
                continue
            if path not in ids:
                ids[path] = len(ids) + 1
                attributes = {"TrackID": str(ids[path]), **_xml_metadata(path)}
                attributes["Location"] = "file://localhost" + quote(
                    path.as_posix(), safe="/:~!$&'()*+,;=@"
                )
                ET.SubElement(collection, "TRACK", attributes)
            results.append(ids[path])
        return results

    for master in masters:
        placements = master.get("genre_placements", [])
        master_node = ET.SubElement(
            playlist_root,
            "NODE",
            {"Type": "0", "Name": master["name"], "Count": str(len(placements))},
        )
        for placement in placements:
            genre = genres[placement["main_genre_id"]]
            subgenres = placement.get("subgenres", [])
            parent_ids = register(placement.get("local_folder"))
            if not subgenres:
                master_node.append(_playlist_node(genre["name"], parent_ids))
                playlist_count += 1
                continue
            genre_node = ET.SubElement(
                master_node,
                "NODE",
                {"Type": "0", "Name": genre["name"], "Count": str(len(subgenres) + 1)},
            )
            genre_node.append(_playlist_node(genre["name"], parent_ids))
            playlist_count += 1
            for subgenre in subgenres:
                genre_node.append(
                    _playlist_node(subgenre["name"], register(subgenre.get("local_folder")))
                )
                playlist_count += 1
    collection.set("Entries", str(len(ids)))
    ET.indent(root, space="  ")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode", short_empty_elements=True)
        + "\n",
        encoding="utf-8",
    )
    return {
        "tracks": len(ids),
        "master_folders": len(masters),
        "playlists": playlist_count,
        "missing_folders": missing,
    }
