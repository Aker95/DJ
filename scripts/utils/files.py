"""Preview and apply collision-safe local file moves."""

from __future__ import annotations

import shutil
from pathlib import Path

from .io import relative_path, sha256_file


def build_move_plan(requests: list[dict], project_root: Path) -> dict:
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
            "source": relative_path(source, project_root),
            "destination": relative_path(destination, project_root),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": sha256_file(source),
        }
        moves.append(move)
        if has_collision:
            collisions.append({**move, "reason": "destination-exists-or-duplicates"})
    return {"moves": moves, "collisions": collisions, "safe_to_apply": not collisions}


def apply_move_plan(plan: dict, project_root: Path) -> list[dict]:
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
        if sha256_file(source) != item["sha256"]:
            raise ValueError(f"Move source hash changed: {item['source']}")
        prepared.append((source, destination, item))
    applied: list[dict] = []
    for source, destination, item in prepared:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        if sha256_file(destination) != item["sha256"]:
            raise RuntimeError(f"Move verification failed: {item['destination']}")
        applied.append(item)
    return applied


def _require_inside(path: Path, root: Path) -> None:
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"Path escapes project root: {path}") from error
