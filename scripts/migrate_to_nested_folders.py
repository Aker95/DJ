#!/usr/bin/env python3
"""
One-time migration: reorganise the flat music/ structure into the 4-folder macro schema.

Before:  music/tech house/
After:   music/DJ PEAK HOUSE - CLUB/tech house/

Reads music_schema_knowledge.json, computes old → new paths for every subgenre,
moves all audio files, and updates local_folder values in the schema in-place.

Run once: python scripts/migrate_to_nested_folders.py
"""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.config import load_schema, save_schema, load_env, PROJECT_ROOT

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".ogg"}


def master_folder_dir(folder_name: str) -> str:
    """'DJ PEAK HOUSE / CLUB' → 'DJ PEAK HOUSE - CLUB'"""
    return folder_name.replace(" / ", " - ")


def subgenre_dir(sg_name: str) -> str:
    return sg_name.lower().replace("/", "-")


def main():
    env = load_env()
    music_root = Path(env["MUSIC_ROOT"])
    schema = load_schema()

    moves = 0
    skipped = 0

    for folder in schema["master_folders"]:
        mf_dir = master_folder_dir(folder["name"])
        for sg in folder["subgenres"]:
            # Determine the new nested path
            sg_dir_name = (
                Path(sg["local_folder"]).name
                if sg.get("local_folder")
                else subgenre_dir(sg["name"])
            )
            new_path = music_root / mf_dir / sg_dir_name
            new_rel = f"music/{mf_dir}/{sg_dir_name}"

            # Determine old path
            if sg.get("local_folder"):
                old_path = PROJECT_ROOT / sg["local_folder"]
            else:
                old_path = music_root / subgenre_dir(sg["name"])

            # Move audio files if old path exists and differs from new path
            if old_path.exists() and old_path != new_path:
                audio_files = [
                    f for f in old_path.iterdir()
                    if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
                ]
                if audio_files:
                    new_path.mkdir(parents=True, exist_ok=True)
                    for f in audio_files:
                        dest = new_path / f.name
                        shutil.move(str(f), str(dest))
                        print(f"  MOVE  {f.relative_to(PROJECT_ROOT)}")
                        print(f"     →  {dest.relative_to(PROJECT_ROOT)}")
                        moves += 1
                else:
                    print(f"  (empty) {old_path.relative_to(PROJECT_ROOT)}")
                    skipped += 1
            elif not old_path.exists():
                print(f"  (new)  {new_rel}")

            # Update schema
            sg["local_folder"] = new_rel

    save_schema(schema)
    print(f"\n---")
    print(f"Files moved:  {moves}")
    print(f"Empty dirs:   {skipped}")
    print(f"Schema updated: music_schema_knowledge.json")
    print(f"\nNext: python scripts/02_verify_structure.py")


if __name__ == "__main__":
    main()
