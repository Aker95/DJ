#!/usr/bin/env python3
"""Preview or apply matches from unsorted local folders."""

from __future__ import annotations

import argparse
from pathlib import Path

from utils.audio import AUDIO_EXTENSIONS
from utils.config import load_config
from utils.files import apply_move_plan, build_move_plan
from utils.io import read_json, relative_path, write_json
from utils.matching import filename_matches
from utils.schema import iter_destinations, load_schema


def unsorted_files(config, schema: dict) -> list[Path]:
    final_folders = {
        (config.project_root / item["local_folder"]).resolve()
        for item in iter_destinations(schema)
        if item.get("local_folder")
    }
    protected = {"intake", "review", "ingestion errors"}
    return sorted(
        path
        for path in config.music_root.rglob("*")
        if path.is_file()
        and path.suffix.casefold() in AUDIO_EXTENSIONS
        and path.parent.resolve() not in final_folders
        and not any(part.casefold() in protected for part in path.relative_to(config.music_root).parts)
    )


def build_reconciliation(delta: dict, config, schema: dict) -> dict:
    candidates = unsorted_files(config, schema)
    used: set[Path] = set()
    requests: list[dict] = []
    unresolved: list[dict] = []
    for track in delta.get("tracks", []):
        matches = [
            path
            for path in candidates
            if path not in used
            and filename_matches(path.name, track.get("artist", ""), track.get("title", ""))
        ]
        if len(matches) != 1:
            unresolved.append(
                {
                    "spotify_track_id": track.get("spotify_track_id"),
                    "artist": track.get("artist"),
                    "title": track.get("title"),
                    "reason": "no-match" if not matches else "multiple-matches",
                    "candidates": [
                        relative_path(path, config.project_root) for path in matches
                    ],
                }
            )
            continue
        source = matches[0]
        used.add(source)
        destination = config.project_root / track["local_folder"] / source.name
        requests.append(
            {
                "source": relative_path(source, config.project_root),
                "destination": relative_path(destination, config.project_root),
                "spotify_track_id": track.get("spotify_track_id"),
            }
        )
    plan = build_move_plan(requests, config.project_root)
    plan["unresolved"] = unresolved
    return plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delta", type=Path, default=Path("data/delta.json"))
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    plan_path = config.data_dir / "reconcile_plan.json"
    if args.apply:
        applied = apply_move_plan(read_json(plan_path), config.project_root)
        print(f"Moved {len(applied)} file(s). Rerun library_delta.py next.")
        return
    plan = build_reconciliation(
        read_json(args.delta), config, load_schema(config.schema_path)
    )
    write_json(plan_path, plan)
    print(
        f"Planned {len(plan['moves'])} move(s); collisions: {len(plan['collisions'])}; "
        f"unresolved: {len(plan['unresolved'])}."
    )


if __name__ == "__main__":
    main()
