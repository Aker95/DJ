#!/usr/bin/env python3
"""Remove legacy schema directories after an approved hierarchy migration.

Only source folders declared by the reviewed migration manifest are eligible.
The cleanup refuses any directory containing content other than ``.DS_Store``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def eligible_directories(manifest_path: Path) -> list[Path]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("artifact_type") != "genre_hierarchy_migration":
        raise ValueError("Not a genre hierarchy migration manifest")

    paths: list[Path] = []
    for node in manifest.get("pending_nodes", []):
        path = Path(node["source_folder"])
        if path.exists() and not path.is_dir():
            raise ValueError(f"Legacy source is not a directory: {path}")
        if not path.exists():
            continue
        unexpected = [item for item in path.iterdir() if item.name != ".DS_Store"]
        if unexpected:
            raise ValueError(
                f"Refusing non-empty legacy directory {path}: "
                + ", ".join(item.name for item in unexpected)
            )
        paths.append(path)
    return paths


def cleanup(manifest_path: Path, *, apply: bool) -> list[Path]:
    paths = eligible_directories(manifest_path)
    if apply:
        for path in paths:
            metadata = path / ".DS_Store"
            if metadata.exists():
                metadata.unlink()
            path.rmdir()
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    paths = cleanup(args.manifest, apply=args.apply)
    action = "Removed" if args.apply else "Would remove"
    print(f"{action} {len(paths)} legacy schema directorie(s):")
    for path in paths:
        print(f"  {path}")


if __name__ == "__main__":
    main()
