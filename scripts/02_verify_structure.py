#!/usr/bin/env python3
"""
02_verify_structure.py

Ensures every schema subgenre has a corresponding local folder under MUSIC_ROOT.
Creates missing folders. Prints a report.

For subgenres with local_folder: null, derives path as MUSIC_ROOT/<lowercase subgenre name>.

Run: python scripts/02_verify_structure.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.config import load_env, load_schema, PROJECT_ROOT
from utils.schema import is_v2, iter_destinations


def main():
    env = load_env()
    schema = load_schema()
    music_root = Path(env["MUSIC_ROOT"])

    existing, created = [], []

    if is_v2(schema):
        grouped = {}
        for destination in iter_destinations(schema, project_root=PROJECT_ROOT):
            grouped.setdefault(destination["master_folder"], []).append(destination)
        folders_by_name = {folder["name"]: folder for folder in schema["master_folders"]}
        folder_rows = [
            (folders_by_name[name], destinations)
            for name, destinations in grouped.items()
        ]
    else:
        folder_rows = [
            (folder, [
                {
                    "name": sg["name"],
                    "local_folder": PROJECT_ROOT / sg["local_folder"]
                    if sg.get("local_folder")
                    else music_root / sg["name"].lower().replace("/", "-"),
                }
                for sg in folder["subgenres"]
            ])
            for folder in schema["master_folders"]
        ]

    for folder, destinations in folder_rows:
        print(f"\n{folder['name']}  (BPM {folder['bpm_min']}–{folder['bpm_max']})")
        for destination in destinations:
            full_path = destination.get("local_folder")
            if full_path is None:
                continue

            if full_path.exists():
                print(f"  ✓  {destination['name']:<30}  {full_path.relative_to(PROJECT_ROOT)}")
                existing.append(full_path)
            else:
                full_path.mkdir(parents=True, exist_ok=True)
                print(f"  +  {destination['name']:<30}  {full_path.relative_to(PROJECT_ROOT)}  [CREATED]")
                created.append(full_path)

    print(f"\n---")
    print(f"Already existed: {len(existing)}")
    print(f"Created:         {len(created)}")


if __name__ == "__main__":
    main()
