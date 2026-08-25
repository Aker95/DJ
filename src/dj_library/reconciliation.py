"""Collision-checked local moves and reviewed cross-system alignment."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .artifacts import (
    now_iso,
    read_json,
    relative_to_root,
    seal_manifest,
    sha256_file,
    verify_manifest,
    write_json,
)
from .audio import AUDIO_EXTENSIONS
from .config import Settings
from .schema import iter_destinations, resolve_destination, validate_schema


def _inside_root(relative_path: str, settings: Settings) -> Path:
    path = (settings.project_root / relative_path).resolve()
    try:
        path.relative_to(settings.project_root)
    except ValueError as error:
        raise ValueError(f"Path escapes project root: {relative_path}") from error
    return path


def build_move_manifest(
    moves: list[dict[str, str]],
    *,
    settings: Settings,
    purpose: str,
) -> dict:
    """Describe exact local moves without changing any file."""

    items: list[dict[str, Any]] = []
    collisions: list[dict[str, str]] = []
    seen_targets: set[str] = set()
    for move in moves:
        source = _inside_root(move["source"], settings)
        destination = _inside_root(move["destination"], settings)
        if not source.is_file():
            raise FileNotFoundError(f"Move source does not exist: {move['source']}")
        destination_key = str(destination).casefold()
        if destination_key in seen_targets:
            collisions.append(
                {
                    "source": move["source"],
                    "destination": move["destination"],
                    "reason": "duplicate-destination",
                }
            )
        seen_targets.add(destination_key)
        source_hash = sha256_file(source)
        if destination.exists():
            collisions.append(
                {
                    "source": move["source"],
                    "destination": move["destination"],
                    "reason": "destination-exists",
                }
            )
        stat = source.stat()
        items.append(
            {
                **move,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": source_hash,
            }
        )
    return seal_manifest(
        {
            "artifact_type": "local_move_manifest",
            "artifact_version": 1,
            "status": "blocked" if collisions else "ready_for_review",
            "created_at": now_iso(),
            "purpose": purpose,
            "moves": items,
            "collisions": collisions,
            "summary": {
                "moves": len(items),
                "total_bytes": sum(item["size"] for item in items),
                "collisions": len(collisions),
            },
        }
    )


def apply_move_manifest(
    manifest_or_path: dict | Path,
    *,
    settings: Settings,
    result_path: Path | None = None,
) -> dict:
    """Apply an exact reviewed move manifest without overwriting files."""

    manifest = (
        read_json(manifest_or_path) if isinstance(manifest_or_path, Path) else manifest_or_path
    )
    verify_manifest(manifest, artifact_type="local_move_manifest")
    if manifest.get("status") != "ready_for_review" or manifest.get("collisions"):
        raise ValueError("Move manifest is not safe to apply")

    prepared: list[tuple[Path, Path, dict]] = []
    already_applied: list[dict] = []
    for item in manifest.get("moves", []):
        source = _inside_root(item["source"], settings)
        destination = _inside_root(item["destination"], settings)
        if destination.is_file():
            if (
                destination.stat().st_size != item["size"]
                or sha256_file(destination) != item["sha256"]
            ):
                raise FileExistsError(f"Move destination collision: {item['destination']}")
            if source.exists():
                raise FileExistsError(f"Both move source and destination exist: {item['source']}")
            already_applied.append(item)
            continue
        if not source.is_file():
            raise FileNotFoundError(f"Move source is missing: {item['source']}")
        stat = source.stat()
        if (
            stat.st_size != item["size"]
            or stat.st_mtime_ns != item["mtime_ns"]
            or sha256_file(source) != item["sha256"]
        ):
            raise ValueError(f"Move source changed after review: {item['source']}")
        prepared.append((source, destination, item))

    moved: list[dict] = []
    for source, destination, item in prepared:
        if destination.exists():
            raise FileExistsError(f"Move destination appeared during apply: {item['destination']}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        if sha256_file(destination) != item["sha256"]:
            raise RuntimeError(f"Move verification failed: {item['destination']}")
        moved.append(item)

    result = {
        "artifact_type": "local_move_apply_result",
        "artifact_version": 1,
        "applied_at": now_iso(),
        "manifest_sha256": manifest["manifest_sha256"],
        "moved": moved,
        "already_applied": already_applied,
        "summary": {"moved": len(moved), "already_applied": len(already_applied)},
    }
    if result_path:
        write_json(result_path, result)
    return result


def build_alignment_manifest(
    review: dict,
    decisions: dict,
    schema: dict,
    *,
    settings: Settings,
) -> dict:
    """Resolve reviewed mismatch decisions into one local/Spotify manifest."""

    review_by_id = {
        item["spotify_id"]: item for item in review.get("tracks", []) if item.get("spotify_id")
    }
    actions: list[dict] = []
    errors: list[str] = []
    for decision in decisions.get("tracks", []):
        spotify_id = decision.get("spotify_id")
        reviewed = review_by_id.get(spotify_id)
        if not reviewed:
            errors.append(f"Unknown reviewed Spotify track {spotify_id!r}")
            continue
        if not decision.get("rationale"):
            errors.append(f"{spotify_id}: rationale is required")
            continue
        destination = resolve_destination(
            schema,
            master_folder=decision["master_folder"],
            main_genre=decision["main_genre"],
            subgenre=decision.get("subgenre"),
        )
        if not destination or not destination.get("spotify_playlist"):
            errors.append(f"{spotify_id}: destination and Spotify playlist must be provisioned")
            continue
        source = _inside_root(reviewed["acquired_file"]["intake_path"], settings)
        target_folder = settings.project_root / destination["local_folder"]
        target = target_folder / source.name
        expected_hash = reviewed["acquired_file"]["sha256"]
        if target.exists() and (sha256_file(target) != expected_hash or source.exists()):
            errors.append(
                f"{spotify_id}: local target collision at {relative_to_root(target, settings)}"
            )
            continue
        initial = reviewed["initial_track"]
        actions.append(
            {
                "spotify_id": spotify_id,
                "source": relative_to_root(source, settings),
                "destination": relative_to_root(target, settings),
                "sha256": expected_hash,
                "initial_placement_id": initial.get("placement_id"),
                "initial_spotify_playlist_id": initial.get("provisional_spotify_playlist_id"),
                "final_placement_id": destination["placement_id"],
                "final_spotify_playlist_id": destination["spotify_playlist"],
                "final_spotify_playlist_name": destination["spotify_playlist_name"],
                "master_folder": destination["master_folder"],
                "main_genre": destination["main_genre"],
                "subgenre": destination.get("subgenre"),
                "rationale": decision["rationale"],
            }
        )
    if errors:
        raise ValueError("Alignment manifest validation failed:\n- " + "\n- ".join(errors))
    return seal_manifest(
        {
            "artifact_type": "reviewed_alignment_manifest",
            "artifact_version": 1,
            "status": "ready_for_review",
            "created_at": now_iso(),
            "tracks": actions,
            "summary": {"tracks": len(actions)},
        }
    )


def playlist_contains(spotify: Any, playlist_id: str, track_id: str) -> bool:
    page = spotify.playlist_items(
        playlist_id,
        fields="items(item(id)),next",
        additional_types=["track"],
        limit=100,
    )
    while page:
        if any((item.get("item") or {}).get("id") == track_id for item in page.get("items", [])):
            return True
        page = spotify.next(page) if page.get("next") else None
    return False


def apply_alignment_manifest(
    manifest: dict,
    *,
    spotify: Any,
    settings: Settings,
) -> dict:
    """Apply Spotify add, local move, old-membership removal, then verify."""

    verify_manifest(manifest, artifact_type="reviewed_alignment_manifest")
    if manifest.get("status") != "ready_for_review":
        raise ValueError("Alignment manifest is not approved for apply")
    results: list[dict] = []
    for item in manifest.get("tracks", []):
        track_id = item["spotify_id"]
        source = _inside_root(item["source"], settings)
        target = _inside_root(item["destination"], settings)
        final_playlist = item["final_spotify_playlist_id"]
        initial_playlist = item.get("initial_spotify_playlist_id")

        if target.exists():
            if sha256_file(target) != item["sha256"]:
                raise FileExistsError(f"Alignment collision: {item['destination']}")
            if source.exists():
                raise FileExistsError(
                    f"Both alignment source and destination exist: {item['source']}"
                )
            local_status = "already_moved"
        elif source.exists():
            if sha256_file(source) != item["sha256"]:
                raise ValueError(f"Alignment source changed: {item['source']}")
            local_status = "move_pending"
        else:
            raise FileNotFoundError(f"Alignment source missing: {item['source']}")

        if not playlist_contains(spotify, final_playlist, track_id):
            spotify._post(
                f"playlists/{final_playlist}/items",
                payload={"uris": [f"spotify:track:{track_id}"]},
            )

        if local_status == "move_pending":
            if target.exists():
                raise FileExistsError(f"Alignment destination appeared: {item['destination']}")
            if not source.exists() or sha256_file(source) != item["sha256"]:
                raise ValueError(f"Alignment source changed during apply: {item['source']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            if sha256_file(target) != item["sha256"]:
                raise RuntimeError(f"Alignment move verification failed: {item['destination']}")
            local_status = "moved"

        if (
            initial_playlist
            and initial_playlist != final_playlist
            and playlist_contains(spotify, initial_playlist, track_id)
        ):
            spotify._delete(
                f"playlists/{initial_playlist}/items",
                payload={"items": [{"uri": f"spotify:track:{track_id}"}]},
            )
        if not playlist_contains(spotify, final_playlist, track_id):
            raise RuntimeError(f"Spotify verification failed for {track_id}")
        results.append({**item, "local_status": local_status, "spotify_status": "verified"})
    return {
        "artifact_type": "reviewed_alignment_apply_result",
        "artifact_version": 1,
        "applied_at": now_iso(),
        "manifest_sha256": manifest["manifest_sha256"],
        "tracks": results,
        "summary": {"aligned": len(results)},
    }


def audit_library(schema: dict, *, settings: Settings, registry: dict | None = None) -> dict:
    """Return a generic current-state inventory without embedded track decisions."""

    validate_schema(schema)
    destinations: list[dict] = []
    known_files: set[Path] = set()
    for destination in iter_destinations(schema, project_root=settings.project_root):
        folder = Path(destination["local_folder"]) if destination.get("local_folder") else None
        files = (
            sorted(
                path
                for path in folder.iterdir()
                if path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS
            )
            if folder and folder.is_dir()
            else []
        )
        known_files.update(path.resolve() for path in files)
        destinations.append(
            {
                "placement_id": destination["placement_id"],
                "local_folder": relative_to_root(folder, settings) if folder else None,
                "folder_exists": bool(folder and folder.is_dir()),
                "audio_files": len(files),
            }
        )
    all_files = (
        {
            path.resolve()
            for path in settings.music_root.rglob("*")
            if path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS
        }
        if settings.music_root.exists()
        else set()
    )
    neutral = sorted(relative_to_root(path, settings) for path in all_files - known_files)
    registered_paths = {
        item.get("local_path")
        for item in (registry or {}).get("tracks", {}).values()
        if item.get("local_path")
    }
    missing_registered = sorted(
        path for path in registered_paths if not (settings.project_root / path).is_file()
    )
    return {
        "artifact_type": "library_audit",
        "artifact_version": 1,
        "created_at": now_iso(),
        "destinations": destinations,
        "neutral_files": neutral,
        "missing_registered_files": missing_registered,
        "summary": {
            "destinations": len(destinations),
            "final_audio_files": len(known_files),
            "neutral_audio_files": len(neutral),
            "missing_registered_files": len(missing_registered),
        },
    }
