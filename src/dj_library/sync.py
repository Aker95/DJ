"""Local/Spotify delta, neutral-file reconciliation, and verification logic."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .artifacts import now_iso, read_json, seal_manifest, write_json
from .audio import AUDIO_EXTENSIONS, analyze_audio
from .config import Settings
from .matching import artist_found_in_stem, match_scores, preprocess_stem
from .reconciliation import apply_move_manifest, build_move_manifest
from .schema import canonical_key, iter_destinations, master_folder_index

_VERSION_TOKENS = {
    "remix",
    "mix",
    "edit",
    "extended",
    "radio",
    "instrumental",
    "live",
    "sped up",
    "slowed",
}


def empty_registry() -> dict:
    return {
        "artifact_type": "music_track_registry",
        "artifact_version": 2,
        "updated_at": now_iso(),
        "tracks": {},
    }


def load_registry(path: Path) -> dict:
    if not path.exists():
        registry = empty_registry()
        write_json(path, registry)
        return registry
    registry = read_json(path)
    if registry.get("artifact_type") != "music_track_registry":
        raise ValueError(f"Invalid track registry: {path}")
    registry.setdefault("tracks", {})
    return registry


def merge_registry(registry: dict, records: list[dict], *, status: str) -> dict:
    for record in records:
        spotify_id = record.get("spotify_track_id") or record.get("spotify_id")
        if not spotify_id:
            continue
        current = registry["tracks"].setdefault(spotify_id, {})
        current.update({key: value for key, value in record.items() if value is not None})
        current["spotify_id"] = spotify_id
        current["workflow_status"] = status
        current["workflow_updated_at"] = now_iso()
    registry["updated_at"] = now_iso()
    return registry


def record_verification(registry: dict, verification: dict) -> dict:
    """Persist every track with the status assigned by verification."""

    for record in verification.get("tracks", []):
        merge_registry(
            registry,
            [record],
            status=record.get("status", "file_review_required"),
        )
    return registry


def pending_acquisition_ids(settings: Settings, registry: dict | None = None) -> set[str]:
    statuses = {
        "downloaded_pending_analysis",
        "verified_consistent",
        "file_review_required",
        "promotion_pending",
    }
    result = {
        spotify_id
        for spotify_id, track in (registry or {}).get("tracks", {}).items()
        if track.get("workflow_status") in statuses
    }
    if settings.runs_root.exists():
        for path in settings.runs_root.glob("acquisition/*/acquisition_manifest.json"):
            try:
                manifest = read_json(path)
            except (OSError, ValueError):
                continue
            for track in manifest.get("tracks", []):
                spotify_id = track.get("spotify_id")
                if spotify_id and track.get("status") in statuses:
                    result.add(spotify_id)
    return result


def _local_stems(folder: Path) -> list[str]:
    if not folder.exists():
        return []
    return [
        path.stem
        for path in folder.iterdir()
        if path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS
    ]


def is_local_match(stems: list[str], artist: str, title: str) -> bool:
    for stem in stems:
        candidate_artist, candidate_title = preprocess_stem(stem)
        artist_score, title_score = match_scores(candidate_artist, candidate_title, artist, title)
        if artist_score >= 0.80 and title_score >= 0.85:
            return True
        if title_score >= 0.95 and artist_found_in_stem(artist, stem):
            return True
    return False


def build_delta(
    snapshot: dict,
    schema: dict,
    *,
    settings: Settings,
    pending_ids: set[str] | None = None,
) -> dict:
    """Compare a snapshot with final local destinations without mutating either."""

    pending = pending_ids or set()
    destinations = {
        item["placement_id"]: item
        for item in iter_destinations(schema, project_root=settings.project_root)
    }
    missing: list[dict] = []
    checked = 0
    skipped = 0
    for playlist in snapshot.get("playlists", []):
        destination = destinations.get(playlist.get("placement_id"))
        if not destination or not destination.get("local_folder"):
            continue
        folder = Path(destination["local_folder"])
        stems = _local_stems(folder)
        for track in playlist.get("tracks", []):
            checked += 1
            spotify_id = track.get("spotify_track_id") or track.get("id")
            if spotify_id in pending:
                skipped += 1
                continue
            if is_local_match(stems, track.get("artist", ""), track.get("title", "")):
                continue
            missing.append(
                {
                    **track,
                    "spotify_id": spotify_id,
                    "main_genre": playlist.get("main_genre"),
                    "subgenre": playlist.get("subgenre"),
                    "classification_level": playlist.get("classification_level"),
                    "master_folder": playlist.get("master_folder"),
                    "placement_id": playlist.get("placement_id"),
                    "provisional_spotify_playlist_id": playlist.get("playlist_id"),
                    "provisional_spotify_playlist_name": playlist.get("expected_playlist_name")
                    or playlist.get("playlist_name"),
                    "local_folder": str(folder.relative_to(settings.project_root)),
                }
            )
    return {
        "artifact_type": "spotify_local_delta",
        "artifact_version": 2,
        "generated_at": now_iso(),
        "tracks": missing,
        "summary": {"checked": checked, "missing": len(missing), "pending_skipped": skipped},
    }


def build_unsorted_reconciliation(
    delta: dict,
    schema: dict,
    *,
    settings: Settings,
) -> tuple[dict, dict]:
    """Plan matches from unsorted folders; intake/review folders are never scanned."""

    schema_folders = {
        Path(item["local_folder"]).resolve()
        for item in iter_destinations(schema, project_root=settings.project_root)
        if item.get("local_folder")
    }
    protected = {"intake", "review", "ingestion errors"}
    unsorted = (
        [
            path
            for path in settings.music_root.iterdir()
            if path.is_dir()
            and path.resolve() not in schema_folders
            and path.name.casefold() not in protected
        ]
        if settings.music_root.exists()
        else []
    )
    destinations = {
        item["placement_id"]: item
        for item in iter_destinations(schema, project_root=settings.project_root)
    }
    used_files: set[Path] = set()
    moves: list[dict[str, str]] = []
    remaining: list[dict] = []
    for track in delta.get("tracks", []):
        found: Path | None = None
        for folder in unsorted:
            for path in sorted(folder.iterdir()):
                if (
                    path in used_files
                    or not path.is_file()
                    or path.suffix.casefold() not in AUDIO_EXTENSIONS
                ):
                    continue
                artist, title = preprocess_stem(path.stem)
                artist_score, title_score = match_scores(
                    artist,
                    title,
                    track.get("artist", ""),
                    track.get("title", ""),
                )
                if (artist_score >= 0.80 and title_score >= 0.85) or (
                    title_score >= 0.95 and artist_found_in_stem(track.get("artist", ""), path.stem)
                ):
                    found = path
                    break
            if found:
                break
        destination = destinations.get(track.get("placement_id"))
        if not found or not destination:
            remaining.append(track)
            continue
        used_files.add(found)
        target = Path(destination["local_folder"]) / found.name
        moves.append(
            {
                "source": str(found.relative_to(settings.project_root)),
                "destination": str(target.relative_to(settings.project_root)),
                "spotify_id": track.get("spotify_id"),
            }
        )
    move_manifest = build_move_manifest(moves, settings=settings, purpose="unsorted-reconciliation")
    updated_delta = {
        **delta,
        "tracks": remaining,
        "reconciled_at": now_iso(),
        "summary": {
            **delta.get("summary", {}),
            "reconciled": len(moves),
            "missing": len(remaining),
        },
    }
    manifest = seal_manifest(
        {
            "artifact_type": "unsorted_reconciliation_manifest",
            "artifact_version": 1,
            "status": move_manifest["status"],
            "created_at": now_iso(),
            "move_manifest": move_manifest,
            "remaining_delta": updated_delta,
            "summary": {"matched": len(moves), "remaining": len(remaining)},
        }
    )
    return manifest, updated_delta


def apply_unsorted_reconciliation(
    manifest: dict,
    *,
    settings: Settings,
    delta_output: Path,
) -> dict:
    from .artifacts import verify_manifest

    verify_manifest(manifest, artifact_type="unsorted_reconciliation_manifest")
    moves = apply_move_manifest(manifest["move_manifest"], settings=settings)
    write_json(delta_output, manifest["remaining_delta"])
    return {
        "artifact_type": "unsorted_reconciliation_apply_result",
        "artifact_version": 1,
        "applied_at": now_iso(),
        "moves": moves,
        "delta_output": str(delta_output),
        "summary": moves["summary"],
    }


def version_tokens(value: str) -> set[str]:
    normalized = f" {canonical_key(value)} "
    return {token for token in _VERSION_TOKENS if f" {canonical_key(token)} " in normalized}


def identity_check(track: dict, file_record: dict, *, settings: Settings) -> dict:
    tags = file_record.get("tags") or {}
    path = settings.project_root / file_record["intake_path"]
    fallback_artist, fallback_title = preprocess_stem(path.stem)
    actual_artist = tags.get("artist") or fallback_artist
    actual_title = tags.get("title") or fallback_title
    artist_score, title_score = match_scores(
        actual_artist,
        actual_title,
        track.get("artist", ""),
        track.get("title", ""),
    )
    expected_duration = track.get("duration_ms")
    actual_duration = file_record.get("duration_ms")
    duration_delta = (
        abs(int(expected_duration) - int(actual_duration)) / 1000
        if expected_duration and actual_duration
        else None
    )
    expected_versions = version_tokens(track.get("title", ""))
    actual_versions = version_tokens(f"{actual_title} {path.stem}")
    compatible = expected_versions.issubset(actual_versions) and not (
        actual_versions - expected_versions - {"mix"}
    )
    identity_match = (artist_score >= 0.80 and title_score >= 0.85) or (
        title_score >= 0.95 and artist_found_in_stem(track.get("artist", ""), path.stem)
    )
    reasons: list[str] = []
    if not identity_match:
        reasons.append("artist-title-mismatch")
    if duration_delta is None:
        reasons.append("duration-unavailable")
    elif duration_delta > 10:
        reasons.append("duration-mismatch")
    if not compatible:
        reasons.append("version-mismatch")
    return {
        "status": "passed" if not reasons else "review",
        "artist_score": round(artist_score, 4),
        "title_score": round(title_score, 4),
        "duration_delta_seconds": duration_delta,
        "expected_version_tokens": sorted(expected_versions),
        "actual_version_tokens": sorted(actual_versions),
        "version_compatible": compatible,
        "fingerprint_status": "unavailable",
        "reasons": reasons,
    }


def _same_master(schema: dict, left: str | None, right: str | None) -> bool:
    masters = master_folder_index(schema)
    left_node = masters.get(canonical_key(left or ""))
    right_node = masters.get(canonical_key(right or ""))
    return bool(left_node and right_node and left_node["id"] == right_node["id"])


def verify_acquisition(
    manifest: dict,
    schema: dict,
    *,
    settings: Settings,
    analyzer: Callable[[Path, dict], dict[str, Any]] = analyze_audio,
    cache: dict[str, dict] | None = None,
) -> tuple[dict, dict, dict, dict]:
    """Verify acquired identity/BPM and split promotion from review outcomes."""

    analysis_cache = cache if cache is not None else {}
    complete: list[dict] = []
    promotion_moves: list[dict] = []
    review: list[dict] = []
    for track in manifest.get("tracks", []):
        file_record = track.get("acquired_file")
        if track.get("status") != "downloaded_pending_analysis" or not file_record:
            continue
        path = settings.project_root / file_record["intake_path"]
        digest = file_record.get("sha256")
        if not path.exists():
            analysis = {"error": "intake-file-missing", "bpm_ambiguous": True}
        else:
            if digest not in analysis_cache:
                try:
                    analysis_cache[digest] = analyzer(path, schema)
                except Exception as error:
                    analysis_cache[digest] = {"error": str(error), "bpm_ambiguous": True}
            analysis = analysis_cache[digest]
        identity = identity_check(track, file_record, settings=settings)
        flags = list(identity["reasons"])
        if analysis.get("error"):
            flags.append("analysis-failed")
        elif analysis.get("bpm_ambiguous"):
            flags.append("ambiguous-bpm")
        elif not _same_master(
            schema, track.get("master_folder"), analysis.get("selected_master_folder")
        ):
            flags.append("bpm-band-mismatch")
        status = "verified_consistent" if not flags else "file_review_required"
        item = {
            "spotify_id": track.get("spotify_id"),
            "status": status,
            "initial_track": track,
            "acquired_file": file_record,
            "identity_check": identity,
            "file_analysis": analysis,
            "review_flags": sorted(set(flags)),
        }
        complete.append(item)
        if status == "verified_consistent":
            promotion_moves.append(
                {
                    "source": file_record["intake_path"],
                    "destination": str(
                        Path(track["intended_local_folder"]) / file_record["file_name"]
                    ),
                    "spotify_id": track.get("spotify_id"),
                    "placement_id": track.get("placement_id"),
                }
            )
        else:
            review.append(item)
    move_manifest = build_move_manifest(
        promotion_moves,
        settings=settings,
        purpose="verified-file-promotion",
    )
    complete_artifact = {
        "artifact_type": "new_file_verification",
        "artifact_version": 2,
        "run_id": manifest.get("run_id"),
        "created_at": now_iso(),
        "tracks": complete,
        "summary": {"verified": len(complete)},
    }
    promotion_artifact = seal_manifest(
        {
            "artifact_type": "consistent_file_promotion_manifest",
            "artifact_version": 2,
            "status": move_manifest["status"],
            "run_id": manifest.get("run_id"),
            "created_at": now_iso(),
            "move_manifest": move_manifest,
            "summary": {"consistent": len(promotion_moves)},
        }
    )
    review_artifact = {
        "artifact_type": "new_file_review_queue",
        "artifact_version": 2,
        "run_id": manifest.get("run_id"),
        "created_at": now_iso(),
        "tracks": review,
        "summary": {"review": len(review)},
    }
    return complete_artifact, promotion_artifact, review_artifact, analysis_cache


def apply_promotion(manifest: dict, *, settings: Settings, registry: dict) -> tuple[dict, dict]:
    from .artifacts import verify_manifest

    verify_manifest(manifest, artifact_type="consistent_file_promotion_manifest")
    move_result = apply_move_manifest(manifest["move_manifest"], settings=settings)
    records = [
        {
            "spotify_id": item.get("spotify_id"),
            "placement_id": item.get("placement_id"),
            "local_path": item["destination"],
        }
        for item in manifest["move_manifest"].get("moves", [])
    ]
    merge_registry(registry, records, status="final_aligned")
    result = {
        "artifact_type": "promotion_apply_result",
        "artifact_version": 2,
        "applied_at": now_iso(),
        "manifest_sha256": manifest["manifest_sha256"],
        "move_result": move_result,
        "summary": move_result["summary"],
    }
    return result, registry
