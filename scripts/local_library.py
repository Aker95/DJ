"""Inspect, structure, reconcile, and safely mutate the canonical local library."""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

from artifacts import (
    fingerprint_artifact,
    fingerprint_data,
    now_iso,
    read_artifact,
    write_artifact,
)
from catalog import list_destinations, load_catalog
from config import SUPPORTED_AUDIO_EXTENSIONS, Config, load_config
from matching import filename_matches


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _build_move_plan(requests: list[dict], project_root: Path) -> dict:
    moves: list[dict] = []
    collisions: list[dict] = []
    targets: set[str] = set()
    for request in requests:
        source = (project_root / request["source"]).resolve()
        destination = (project_root / request["destination"]).resolve()
        _require_inside(source, project_root)
        _require_inside(destination, project_root)
        if not source.is_file():
            raise FileNotFoundError(f"Move source does not exist: {request['source']}")
        target_key = str(destination).casefold()
        has_collision = destination.exists() or target_key in targets
        targets.add(target_key)
        stat = source.stat()
        move = {
            **request,
            "source": _relative_path(source, project_root),
            "destination": _relative_path(destination, project_root),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": _sha256_file(source),
        }
        moves.append(move)
        if has_collision:
            collisions.append({**move, "reason": "destination-exists-or-duplicates"})
    return {"moves": moves, "collisions": collisions, "safe_to_apply": not collisions}


def _apply_move_plan(plan: dict, project_root: Path) -> list[dict]:
    if not plan.get("safe_to_apply") or plan.get("collisions"):
        raise ValueError("The move plan contains collisions")
    prepared: list[tuple[Path, Path, dict]] = []
    for item in plan.get("moves", []):
        source = (project_root / item["source"]).resolve()
        destination = (project_root / item["destination"]).resolve()
        _require_inside(source, project_root)
        _require_inside(destination, project_root)
        if destination.exists():
            raise FileExistsError(f"Move destination exists: {item['destination']}")
        if not source.is_file():
            raise FileNotFoundError(f"Move source is missing: {item['source']}")
        stat = source.stat()
        if stat.st_size != item["size"] or stat.st_mtime_ns != item["mtime_ns"]:
            raise ValueError(f"Move source changed: {item['source']}")
        if _sha256_file(source) != item["sha256"]:
            raise ValueError(f"Move source hash changed: {item['source']}")
        prepared.append((source, destination, item))
    applied: list[dict] = []
    for source, destination, item in prepared:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        if _sha256_file(destination) != item["sha256"]:
            raise RuntimeError(f"Move verification failed: {item['destination']}")
        applied.append(item)
    return applied


def _require_inside(path: Path, root: Path) -> None:
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"Path escapes project root: {path}") from error


def build_structure_plan(catalog: dict, project_root: Path) -> dict:
    folders = sorted(
        {
            str(
                (project_root / item["local_folder"])
                .resolve()
                .relative_to(project_root.resolve())
            )
            for item in list_destinations(catalog)
            if item.get("local_folder")
            and not (project_root / item["local_folder"]).is_dir()
        }
    )
    return {
        "created_at": now_iso(),
        "actions": [{"folder": folder} for folder in folders],
        "summary": {"folders": len(folders)},
    }


def apply_structure_plan(plan: dict, project_root: Path) -> dict:
    created: list[str] = []
    for action in plan.get("actions", []):
        folder = (project_root / action["folder"]).resolve()
        _require_inside(folder, project_root)
        folder.mkdir(parents=True, exist_ok=True)
        created.append(action["folder"])
    return {
        "applied_at": now_iso(),
        "created": created,
        "summary": {"folders": len(created)},
    }


def _folder_contains_track(folder: Path, artist: str, title: str) -> bool:
    if not folder.is_dir():
        return False
    return any(
        path.is_file()
        and path.suffix.casefold() in SUPPORTED_AUDIO_EXTENSIONS
        and filename_matches(path.name, artist, title)
        for path in folder.iterdir()
    )


def build_delta(snapshot: dict, project_root: Path) -> dict:
    missing: list[dict] = []
    checked = 0
    for playlist in snapshot.get("playlists", []):
        folder = (project_root / playlist["local_folder"]).resolve()
        _require_inside(folder, project_root)
        for track in playlist.get("tracks", []):
            checked += 1
            if _folder_contains_track(
                folder, track.get("artist", ""), track.get("title", "")
            ):
                continue
            missing.append(
                {
                    **track,
                    "master_folder": playlist["master_folder"],
                    "main_genre": playlist["main_genre"],
                    "subgenre": playlist.get("subgenre"),
                    "placement_id": playlist["placement_id"],
                    "source_playlist_id": playlist["spotify_playlist_id"],
                    "local_folder": playlist["local_folder"],
                }
            )
    return {
        "created_at": now_iso(),
        "tracks": missing,
        "summary": {"checked": checked, "missing": len(missing)},
    }


def _unsorted_files(config: Config, catalog: dict) -> list[Path]:
    final_folders = {
        (config.project_root / item["local_folder"]).resolve()
        for item in list_destinations(catalog)
        if item.get("local_folder")
    }
    protected = {"intake", "review", "ingestion errors"}
    return sorted(
        path
        for path in config.music_root.rglob("*")
        if path.is_file()
        and path.suffix.casefold() in SUPPORTED_AUDIO_EXTENSIONS
        and path.parent.resolve() not in final_folders
        and not any(
            part.casefold() in protected
            for part in path.relative_to(config.music_root).parts
        )
    )


def build_reconciliation_plan(delta: dict, config: Config, catalog: dict) -> dict:
    candidates = _unsorted_files(config, catalog)
    used: set[Path] = set()
    requests, unresolved = [], []
    for track in delta.get("tracks", []):
        matches = [
            path
            for path in candidates
            if path not in used
            and filename_matches(
                path.name, track.get("artist", ""), track.get("title", "")
            )
        ]
        if len(matches) != 1:
            unresolved.append(
                {
                    "spotify_track_id": track.get("spotify_track_id"),
                    "artist": track.get("artist"),
                    "title": track.get("title"),
                    "reason": "no-match" if not matches else "multiple-matches",
                    "candidates": [
                        _relative_path(path, config.project_root) for path in matches
                    ],
                }
            )
            continue
        source = matches[0]
        used.add(source)
        destination = config.project_root / track["local_folder"] / source.name
        requests.append(
            {
                "source": _relative_path(source, config.project_root),
                "destination": _relative_path(destination, config.project_root),
                "spotify_track_id": track.get("spotify_track_id"),
            }
        )
    plan = _build_move_plan(requests, config.project_root)
    plan.update(
        {
            "created_at": now_iso(),
            "actions": plan.pop("moves"),
            "unresolved": unresolved,
        }
    )
    plan["summary"] = {
        "moves": len(plan["actions"]),
        "collisions": len(plan["collisions"]),
        "unresolved": len(unresolved),
    }
    return plan


def apply_reconciliation_plan(plan: dict, project_root: Path) -> dict:
    move_plan = {
        "moves": plan.get("actions", []),
        "collisions": plan.get("collisions", []),
        "safe_to_apply": plan.get("safe_to_apply"),
    }
    applied = _apply_move_plan(move_plan, project_root)
    return {
        "applied_at": now_iso(),
        "moves": applied,
        "summary": {"moved": len(applied)},
    }


def promote_verified_file(
    source: Path, destination_folder: Path, project_root: Path
) -> dict:
    destination = destination_folder / source.name
    plan = _build_move_plan(
        [
            {
                "source": _relative_path(source, project_root),
                "destination": _relative_path(destination, project_root),
            }
        ],
        project_root,
    )
    if not plan["safe_to_apply"]:
        raise FileExistsError(f"Destination collision: {destination}")
    return _apply_move_plan(plan, project_root)[0]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    structure = commands.add_parser("structure")
    structure.add_argument("--apply", action="store_true")
    structure.add_argument(
        "--plan", type=Path, default=Path("data/library_structure_plan.json")
    )
    delta = commands.add_parser("delta")
    delta.add_argument(
        "snapshot", type=Path, nargs="?", default=Path("data/spotify_snapshot.json")
    )
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument(
        "--delta", type=Path, default=Path("data/library_delta.json")
    )
    reconcile.add_argument("--apply", action="store_true")
    reconcile.add_argument(
        "--plan", type=Path, default=Path("data/library_reconcile_plan.json")
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    config = load_config()
    catalog = load_catalog(config.schema_path)
    catalog_fingerprint = fingerprint_data(catalog)
    if args.command == "structure":
        if args.apply:
            plan = read_artifact(args.plan, "library-structure-plan")
            if (plan.get("inputs") or {}).get("catalog") != catalog_fingerprint:
                raise ValueError("Library structure plan is stale; preview it again")
            result = apply_structure_plan(plan, config.project_root)
            write_artifact(
                config.data_dir / "library_structure_result.json",
                "library-structure-result",
                result,
            )
            print(f"Created {result['summary']['folders']} folder(s).")
            return
        plan = build_structure_plan(catalog, config.project_root)
        plan["inputs"] = {"catalog": catalog_fingerprint}
        write_artifact(args.plan, "library-structure-plan", plan)
        print(f"Missing folders: {plan['summary']['folders']}.")
        return
    if args.command == "delta":
        snapshot = read_artifact(args.snapshot, "spotify-snapshot")
        delta = build_delta(snapshot, config.project_root)
        write_artifact(config.data_dir / "library_delta.json", "library-delta", delta)
        print(
            f"Checked {delta['summary']['checked']} tracks; {delta['summary']['missing']} missing."
        )
        return
    if args.apply:
        plan = read_artifact(args.plan, "library-reconciliation-plan")
        expected = {
            "catalog": catalog_fingerprint,
            "delta": fingerprint_artifact(
                Path((plan.get("input_paths") or {})["delta"])
            ),
        }
        if plan.get("inputs") != expected:
            raise ValueError("Library reconciliation plan is stale; preview it again")
        result = apply_reconciliation_plan(plan, config.project_root)
        write_artifact(
            config.data_dir / "library_reconcile_result.json",
            "library-reconciliation-result",
            result,
        )
        print(f"Moved {result['summary']['moved']} file(s).")
        return
    delta = read_artifact(args.delta, "library-delta")
    plan = build_reconciliation_plan(delta, config, catalog)
    plan["inputs"] = {
        "catalog": catalog_fingerprint,
        "delta": fingerprint_artifact(args.delta),
    }
    plan["input_paths"] = {"delta": str(args.delta.resolve())}
    write_artifact(args.plan, "library-reconciliation-plan", plan)
    print(
        f"Planned {plan['summary']['moves']} move(s); collisions: {plan['summary']['collisions']}; "
        f"unresolved: {plan['summary']['unresolved']}."
    )


if __name__ == "__main__":
    main()
