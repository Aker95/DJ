#!/usr/bin/env python3
"""Manifest-driven migration into main-genre/subgenre local folders.

Dry-run is the default and only writes a reviewable JSON manifest.  Applying
requires the exact manifest path and never deletes the old empty directories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.config import PROJECT_ROOT, load_schema, save_schema
from utils.schema import is_v2, iter_genre_placements, iter_subgenres


AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".ogg"}
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "music_schema_knowledge.json"
DEFAULT_MANIFEST_DIR = PROJECT_ROOT / "migration_manifests"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(data: dict) -> str:
    payload = {key: value for key, value in data.items() if key != "manifest_sha256"}
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))


def _pending_nodes(schema: dict):
    for context in iter_genre_placements(schema):
        placement = context["placement"]
        if placement.get("migration_status") == "pending" and placement.get(
            "legacy_local_folder"
        ):
            yield placement
    for context in iter_subgenres(schema):
        node = context["subgenre"]
        if node.get("migration_status") == "pending" and node.get(
            "legacy_local_folder"
        ):
            yield node


def _already_aligned_nodes(schema: dict):
    """Yield destinations whose legacy parent path already is the v2 path."""
    for context in iter_genre_placements(schema):
        placement = context["placement"]
        if (
            placement.get("migration_status") == "complete"
            and placement.get("legacy_local_folder")
            and placement.get("legacy_local_folder") == placement.get("local_folder")
        ):
            yield placement
    for context in iter_subgenres(schema):
        node = context["subgenre"]
        if (
            node.get("migration_status") == "complete"
            and node.get("legacy_local_folder")
            and node.get("legacy_local_folder") == node.get("local_folder")
        ):
            yield node


def build_manifest(schema_path: Path = DEFAULT_SCHEMA_PATH) -> dict:
    """Build a collision-checked, non-mutating migration manifest."""
    schema = load_schema(schema_path)
    if not is_v2(schema):
        raise ValueError("Hierarchy migration requires schema v2")

    moves: list[dict] = []
    collisions: list[dict] = []
    seen_destinations: dict[Path, Path] = {}
    pending_nodes: list[dict] = []
    already_in_place: list[dict] = []

    for node in _pending_nodes(schema):
        source_dir = PROJECT_ROOT / node["legacy_local_folder"]
        destination_dir = PROJECT_ROOT / node["local_folder"]
        pending_nodes.append(
            {
                "id": node["id"],
                "source_folder": _relative(source_dir),
                "destination_folder": _relative(destination_dir),
            }
        )
        if not source_dir.exists():
            continue
        if source_dir.is_symlink() or destination_dir.is_symlink():
            collisions.append(
                {
                    "source": _relative(source_dir),
                    "destination": _relative(destination_dir),
                    "reason": "symlinked folders are not eligible",
                }
            )
            continue

        for source in sorted(source_dir.iterdir()):
            if not source.is_file() or source.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            destination = destination_dir / source.name
            previous_source = seen_destinations.get(destination.resolve())
            if previous_source and previous_source.resolve() != source.resolve():
                collisions.append(
                    {
                        "source": _relative(source),
                        "destination": _relative(destination),
                        "reason": f"destination also claimed by {_relative(previous_source)}",
                    }
                )
                continue
            seen_destinations[destination.resolve()] = source
            if destination.exists():
                collisions.append(
                    {
                        "source": _relative(source),
                        "destination": _relative(destination),
                        "reason": "destination already exists",
                    }
                )
                continue
            stat = source.stat()
            moves.append(
                {
                    "source": _relative(source),
                    "destination": _relative(destination),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            )

    for node in _already_aligned_nodes(schema):
        folder = PROJECT_ROOT / node["local_folder"]
        if not folder.exists() or folder.is_symlink():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            stat = path.stat()
            already_in_place.append(
                {
                    "path": _relative(path),
                    "destination": _relative(path),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "reason": "v2 parent destination already matches the legacy path",
                }
            )

    manifest = {
        "artifact_type": "genre_hierarchy_migration",
        "artifact_version": 1,
        "status": "blocked_by_collisions" if collisions else "ready_for_review",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "schema_path": _relative(schema_path),
        "schema_sha256_before": _file_sha256(schema_path),
        "pending_nodes": pending_nodes,
        "moves": moves,
        "already_in_place": already_in_place,
        "collisions": collisions,
        "summary": {
            "pending_nodes": len(pending_nodes),
            "audio_files": len(moves) + len(already_in_place),
            "moves": len(moves),
            "already_in_place": len(already_in_place),
            "bytes": sum(move["size"] for move in moves)
            + sum(item["size"] for item in already_in_place),
            "collisions": len(collisions),
        },
    }
    manifest["manifest_sha256"] = _json_sha256(manifest)
    return manifest


def write_manifest(manifest: dict, path: Path | None = None) -> Path:
    if path is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = DEFAULT_MANIFEST_DIR / f"genre_hierarchy_{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return path


def apply_manifest(manifest_path: Path) -> dict:
    """Apply a reviewed manifest without overwriting or deleting anything."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("artifact_type") != "genre_hierarchy_migration":
        raise ValueError("Not a genre hierarchy migration manifest")
    if manifest.get("manifest_sha256") != _json_sha256(manifest):
        raise ValueError("Manifest hash mismatch; regenerate and review it")
    if manifest.get("collisions"):
        raise ValueError("Manifest contains collisions and cannot be applied")

    schema_path = PROJECT_ROOT / manifest["schema_path"]
    schema = load_schema(schema_path)
    if not is_v2(schema):
        raise ValueError("Hierarchy migration requires schema v2")
    current_schema_hash = _file_sha256(schema_path)
    if current_schema_hash != manifest["schema_sha256_before"]:
        # A successful apply necessarily changes the schema hash.  Recognize
        # that exact completed state so re-running a reviewed manifest is a
        # harmless no-op, even if later approved taxonomy edits also changed
        # the schema.  Any partial or ambiguous state still fails closed.
        nodes_by_id: dict[str, dict] = {}
        for context in iter_genre_placements(schema):
            placement = context["placement"]
            if placement.get("id"):
                nodes_by_id[placement["id"]] = placement
        for context in iter_subgenres(schema):
            node = context["subgenre"]
            if node.get("id"):
                nodes_by_id[node["id"]] = node

        reviewed_nodes_complete = all(
            node_data["id"] in nodes_by_id
            and nodes_by_id[node_data["id"]].get("migration_status") == "complete"
            and not nodes_by_id[node_data["id"]].get("legacy_local_folder")
            for node_data in manifest["pending_nodes"]
        )
        reviewed_files_complete = all(
            not (PROJECT_ROOT / move["source"]).exists()
            and (PROJECT_ROOT / move["destination"]).is_file()
            and (PROJECT_ROOT / move["destination"]).stat().st_size == move["size"]
            for move in manifest["moves"]
        )
        if reviewed_nodes_complete and reviewed_files_complete:
            return {
                "moved": 0,
                "already_applied": len(manifest["moves"]),
                "schema_updated": False,
                "old_directories_deleted": False,
            }
        raise ValueError("Schema changed after the manifest was generated")

    moved = 0
    already_applied = 0
    for move in manifest["moves"]:
        source = PROJECT_ROOT / move["source"]
        destination = PROJECT_ROOT / move["destination"]
        if source.exists():
            stat = source.stat()
            if stat.st_size != move["size"] or stat.st_mtime_ns != move["mtime_ns"]:
                raise ValueError(f"Source changed since review: {move['source']}")
            if destination.exists():
                raise FileExistsError(f"Refusing to overwrite {move['destination']}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            moved += 1
        elif destination.exists() and destination.stat().st_size == move["size"]:
            already_applied += 1
        else:
            raise FileNotFoundError(
                f"Neither reviewed source nor completed destination exists: {move['source']}"
            )

    pending_ids = {node["id"] for node in manifest["pending_nodes"]}
    for node in _pending_nodes(schema):
        if node.get("id") in pending_ids:
            node["migration_status"] = "complete"
            node.pop("legacy_local_folder", None)
    schema["_meta"]["folder_migration_status"] = "complete"
    schema["_meta"]["last_updated"] = datetime.now(timezone.utc).date().isoformat()
    save_schema(schema, schema_path)

    result = {
        "moved": moved,
        "already_applied": already_applied,
        "schema_updated": True,
        "old_directories_deleted": False,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply the reviewed main-genre folder migration."
    )
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA_PATH))
    parser.add_argument("--manifest", help="Dry-run output manifest path.")
    parser.add_argument(
        "--apply",
        metavar="MANIFEST",
        help="Apply the exact reviewed manifest. Without this flag only a dry-run is generated.",
    )
    args = parser.parse_args()

    if args.apply:
        result = apply_manifest(Path(args.apply))
        print(json.dumps(result, indent=2))
        return

    manifest = build_manifest(Path(args.schema))
    path = write_manifest(manifest, Path(args.manifest) if args.manifest else None)
    print(f"Migration dry-run written to {path}")
    print(json.dumps(manifest["summary"], indent=2))
    if manifest["collisions"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
