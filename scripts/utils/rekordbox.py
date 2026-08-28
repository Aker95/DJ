"""Read-only Rekordbox inspection and XML export helpers."""

from __future__ import annotations

import re
import shutil
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

from mutagen import File as MutagenFile

from .audio import AUDIO_EXTENSIONS
from .config import Config
from .io import now_iso, relative_path
from .matching import match_scores
from .schema import bpm_in_master, bpm_master, iter_destinations, load_schema

KIND_BY_EXTENSION = {
    ".aac": "AAC File", ".aif": "AIFF File", ".aiff": "AIFF File", ".alac": "ALAC File",
    ".flac": "FLAC File", ".m4a": "M4A File", ".mp3": "MP3 File", ".ogg": "OGG File",
    ".opus": "Opus File", ".wav": "WAV File",
}


def read_tracks(database_path: Path, config: Config, schema: dict) -> list[dict]:  # pragma: no cover
    """Read a temporary database copy so the live Rekordbox database remains untouched."""

    try:
        import pyrekordbox.db6.database as database_module
        from pyrekordbox import Rekordbox6Database
    except ImportError as error:
        raise RuntimeError("pyrekordbox is required for Rekordbox review") from error
    destinations = {
        Path(item["local_folder"]).resolve(): item
        for item in iter_destinations(schema, config.project_root)
        if item.get("local_folder")
    }
    with tempfile.TemporaryDirectory(prefix="dj-rekordbox-readonly-") as temporary:
        snapshot = Path(temporary) / "master.db"
        shutil.copy2(database_path, snapshot)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(database_path) + suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, Path(str(snapshot) + suffix))
        database_module.get_rekordbox_pid = lambda: None
        database = Rekordbox6Database(path=snapshot)
        try:
            return [
                record
                for content in database.get_content()
                if (record := _content_record(content, config, destinations)) is not None
            ]
        finally:
            database.close()


def _content_record(content: Any, config: Config, destinations: dict) -> dict | None:
    path = Path(str(content.FolderPath or "")).resolve()
    try:
        path.relative_to(config.music_root)
    except (OSError, ValueError):
        return None
    if not path.is_file():
        return None
    destination = destinations.get(path.parent)
    try:
        artist = content.Artist.Name if content.Artist else ""
    except Exception:
        artist = ""
    raw_bpm = float(content.BPM) / 100 if content.BPM else None
    normalized_bpm = raw_bpm * 2 if raw_bpm and raw_bpm < 118 and raw_bpm * 2 <= 180 else raw_bpm
    return {
        "rekordbox_content_id": str(content.ID),
        "path": relative_path(path, config.project_root),
        "artist": artist,
        "title": content.Title or path.stem,
        "duration_ms": int(content.Length or 0) * 1000 or None,
        "isrc": (content.ISRC or "").strip().upper() or None,
        "rekordbox_bpm": round(normalized_bpm, 4) if normalized_bpm else None,
        "current_destination": _public_destination(destination),
    }


def _public_destination(destination: dict | None) -> dict | None:
    if not destination:
        return None
    return {
        key: destination.get(key)
        for key in ("placement_id", "master_folder", "main_genre", "subgenre", "local_folder")
    }


def review_tracks(
    tracks: list[dict],
    schema: dict,
    classifications: dict | None = None,
    acquisition_results: dict | None = None,
) -> dict:
    approved = (classifications or {}).get("tracks", [])
    acquired_by_id = {
        item.get("spotify_track_id"): item
        for item in (acquisition_results or {}).get("tracks", [])
        if item.get("spotify_track_id")
    }
    records: list[dict] = []
    for track in tracks:
        flags: list[str] = []
        current = track.get("current_destination")
        bpm = track.get("rekordbox_bpm")
        if not current:
            flags.append("schema-path-missing")
        if bpm is None:
            flags.append("rekordbox-bpm-missing")
        elif current:
            current_master = next(
                (master for master in schema["master_folders"] if master["name"] == current["master_folder"]),
                None,
            )
            if not current_master or not bpm_in_master(current_master, bpm):
                flags.append("bpm-band-mismatch")
        identity = match_classification(track, approved)
        if identity.get("status") == "ambiguous":
            flags.append("identity-ambiguous")
        elif identity.get("status") == "matched":
            expected = identity["classification"]
            if current and current["placement_id"] != expected.get("placement_id"):
                flags.append("classification-path-mismatch")
            if bpm_master(schema, bpm) and expected.get("master_folder") != bpm_master(schema, bpm)["name"]:
                flags.append("spotify-rekordbox-bpm-mismatch")
        acquisition = None
        if identity.get("status") == "matched":
            acquisition = acquired_by_id.get(
                identity["classification"].get("spotify_track_id")
            )
        acquisition_flags = set((acquisition or {}).get("review_flags", []))
        if "version-mismatch" in acquisition_flags:
            flags.append("possible-wrong-version")
        if acquisition_flags & {"artist-title-mismatch", "duration-mismatch"}:
            flags.append("possible-wrong-download")
        records.append(
            {
                **track,
                "classification_match": identity,
                "acquisition_match": acquisition,
                "review_flags": flags,
                "status": "review-required" if flags else "consistent",
            }
        )
    counts = Counter(item["status"] for item in records)
    return {
        "created_at": now_iso(),
        "tracks": records,
        "review_tracks": [item for item in records if item["review_flags"]],
        "summary": {"files": len(records), **dict(counts)},
    }


def match_classification(local: dict, classifications: list[dict]) -> dict:
    exact = [
        item for item in classifications if local.get("isrc") and item.get("isrc") == local["isrc"]
    ]
    if len(exact) == 1:
        return {"status": "matched", "method": "isrc", "classification": exact[0]}
    candidates: list[tuple[float, dict]] = []
    for item in classifications:
        artist_score, title_score = match_scores(
            local.get("artist", ""), local.get("title", ""), item.get("artist", ""), item.get("title", "")
        )
        duration = 1.0
        if local.get("duration_ms") and item.get("duration_ms"):
            duration = max(0, 1 - abs(local["duration_ms"] - item["duration_ms"]) / 15000)
        if artist_score >= 0.80 and title_score >= 0.85 and duration >= 0.5:
            candidates.append((artist_score * 0.4 + title_score * 0.5 + duration * 0.1, item))
    candidates.sort(key=lambda value: value[0], reverse=True)
    if not candidates:
        return {"status": "unmatched"}
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.03:
        return {"status": "ambiguous"}
    return {"status": "matched", "method": "metadata", "classification": candidates[0][1]}


def export_xml(config: Config, schema: dict, output_path: Path) -> dict:
    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    ET.SubElement(root, "PRODUCT", {"Name": "DJ Library", "Version": "1.0", "Company": "Personal"})
    collection = ET.SubElement(root, "COLLECTION", {"Entries": "0"})
    playlists = ET.SubElement(root, "PLAYLISTS")
    master_root = ET.SubElement(
        playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": str(len(schema["master_folders"]))}
    )
    track_ids: dict[Path, int] = {}
    playlist_count = 0
    destinations = list(iter_destinations(schema, config.project_root))
    for master in schema["master_folders"]:
        master_destinations = [item for item in destinations if item["master_folder"] == master["name"]]
        genres = sorted({item["main_genre"] for item in master_destinations})
        master_node = ET.SubElement(
            master_root, "NODE", {"Type": "0", "Name": master["name"], "Count": str(len(genres))}
        )
        for genre in genres:
            genre_destinations = [item for item in master_destinations if item["main_genre"] == genre]
            parent = next(item for item in genre_destinations if item["subgenre"] is None)
            leaves = [item for item in genre_destinations if item["subgenre"]]
            if not leaves:
                master_node.append(_playlist_node(genre, _register(parent, track_ids, collection)))
                playlist_count += 1
                continue
            genre_node = ET.SubElement(
                master_node, "NODE", {"Type": "0", "Name": genre, "Count": str(len(leaves) + 1)}
            )
            genre_node.append(_playlist_node(genre, _register(parent, track_ids, collection)))
            playlist_count += 1
            for leaf in leaves:
                genre_node.append(_playlist_node(leaf["subgenre"], _register(leaf, track_ids, collection)))
                playlist_count += 1
    collection.set("Entries", str(len(track_ids)))
    ET.indent(root, space="  ")
    output_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode", short_empty_elements=True)
        + "\n",
        encoding="utf-8",
    )
    return {"tracks": len(track_ids), "playlists": playlist_count}


def _register(destination: dict, ids: dict[Path, int], collection: ET.Element) -> list[int]:
    folder = Path(destination["local_folder"])
    if not folder.is_dir():
        return []
    result: list[int] = []
    for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.casefold() not in AUDIO_EXTENSIONS:
            continue
        if path not in ids:
            ids[path] = len(ids) + 1
            attributes = {"TrackID": str(ids[path]), **_xml_metadata(path)}
            attributes["Location"] = "file://localhost" + quote(path.resolve().as_posix(), safe="/:~!$&'()*+,;=@")
            ET.SubElement(collection, "TRACK", attributes)
        result.append(ids[path])
    return result


def _xml_metadata(path: Path) -> dict[str, str]:
    metadata = {
        "Name": path.stem,
        "Kind": KIND_BY_EXTENSION.get(path.suffix.casefold(), "Audio File"),
        "Size": str(path.stat().st_size),
        "DateModified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
    }
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        return metadata
    if audio is None:
        return metadata
    tags = audio.tags or {}
    for output, keys in (("Name", ("title",)), ("Artist", ("artist", "albumartist")), ("Album", ("album",))):
        value = next((tags.get(key) for key in keys if tags.get(key)), None)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        if value:
            metadata[output] = str(value)
    info = getattr(audio, "info", None)
    if getattr(info, "length", None):
        metadata["TotalTime"] = str(round(float(info.length)))
    if getattr(info, "bitrate", None):
        metadata["BitRate"] = str(round(float(info.bitrate) / 1000))
    if getattr(info, "sample_rate", None):
        metadata["SampleRate"] = str(round(float(info.sample_rate)))
    return metadata


def _playlist_node(name: str, ids: list[int]) -> ET.Element:
    node = ET.Element("NODE", {"Type": "1", "Name": name, "Entries": str(len(ids)), "KeyType": "0"})
    for track_id in ids:
        ET.SubElement(node, "TRACK", {"Key": str(track_id)})
    return node
