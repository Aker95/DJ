#!/usr/bin/env python3
"""Preview or create local folders required by the schema."""

from __future__ import annotations

import argparse
from pathlib import Path

from utils.config import load_config
from utils.schema import iter_destinations, load_schema


def missing_folders(schema: dict, project_root: Path) -> list[Path]:
    return sorted(
        {
            (project_root / destination["local_folder"]).resolve()
            for destination in iter_destinations(schema)
            if destination.get("local_folder")
            and not (project_root / destination["local_folder"]).is_dir()
        }
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    folders = missing_folders(load_schema(config.schema_path), config.project_root)
    for folder in folders:
        print(f"{'Create' if args.apply else 'Would create'}: {folder.relative_to(config.project_root)}")
        if args.apply:
            folder.mkdir(parents=True, exist_ok=True)
    print(f"{'Created' if args.apply else 'Missing'} folders: {len(folders)}")


if __name__ == "__main__":
    main()
