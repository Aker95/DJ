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


def main():
    env = load_env()
    schema = load_schema()
    music_root = Path(env["MUSIC_ROOT"])

    existing, created = [], []

    for folder in schema["master_folders"]:
        mf_dir = folder["name"].replace(" / ", " - ")
        print(f"\n{folder['name']}  (BPM {folder['bpm_min']}–{folder['bpm_max']})")
        for sg in folder["subgenres"]:
            local_folder = sg.get("local_folder")
            if local_folder:
                # local_folder is relative to project root e.g. "music/DJ PEAK HOUSE - CLUB/tech house"
                full_path = PROJECT_ROOT / local_folder
            else:
                # Derive: music/<master folder dir>/<subgenre name>
                derived_name = sg["name"].lower().replace("/", "-")
                full_path = music_root / mf_dir / derived_name

            if full_path.exists():
                print(f"  ✓  {sg['name']:<30}  {full_path.relative_to(PROJECT_ROOT)}")
                existing.append(full_path)
            else:
                full_path.mkdir(parents=True, exist_ok=True)
                print(f"  +  {sg['name']:<30}  {full_path.relative_to(PROJECT_ROOT)}  [CREATED]")
                created.append(full_path)

    print(f"\n---")
    print(f"Already existed: {len(existing)}")
    print(f"Created:         {len(created)}")


if __name__ == "__main__":
    main()
