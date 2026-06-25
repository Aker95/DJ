#!/usr/bin/env python3
"""
03_delta_scan.py

Compares tracks in spotify_snapshot.json against local schema-aligned folders.
Writes delta.json with tracks that are on Spotify but missing locally.

Matching uses normalized fuzzy comparison (difflib, threshold 0.85) so minor
filename variations don't generate false deltas.

Run: python scripts/03_delta_scan.py
"""

import json
import re
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.config import load_env, PROJECT_ROOT

SNAPSHOT_PATH = PROJECT_ROOT / "spotify_snapshot.json"
DELTA_PATH = PROJECT_ROOT / "delta.json"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a"}


def normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def load_local_stems(folder_path: Path) -> list[str]:
    if not folder_path.exists():
        return []
    return [
        normalize(f.stem)
        for f in folder_path.iterdir()
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    ]


def is_local_match(local_stems: list[str], artist: str, title: str, threshold: float = 0.85) -> bool:
    canonical = normalize(f"{artist} - {title}")
    for stem in local_stems:
        if SequenceMatcher(None, canonical, stem).ratio() >= threshold:
            return True
    return False


def main():
    env = load_env()
    music_root = Path(env["MUSIC_ROOT"])

    if not SNAPSHOT_PATH.exists():
        print("ERROR: spotify_snapshot.json not found. Run 01_spotify_snapshot.py first.")
        sys.exit(1)

    snapshot = json.loads(SNAPSHOT_PATH.read_text())
    delta_tracks = []
    total_checked = 0

    for pl in snapshot["playlists"]:
        local_folder = pl.get("local_folder")
        if local_folder:
            folder_path = PROJECT_ROOT / local_folder
        else:
            folder_path = music_root / pl["schema_subgenre"].lower().replace("/", "-")

        local_stems = load_local_stems(folder_path)
        missing_in_playlist = 0

        for track in pl["tracks"]:
            total_checked += 1
            if not is_local_match(local_stems, track["artist"], track["title"]):
                missing_in_playlist += 1
                delta_tracks.append({
                    "artist": track["artist"],
                    "title": track["title"],
                    "spotify_id": track["id"],
                    "subgenre": pl["schema_subgenre"],
                    "master_folder": pl["master_folder"],
                    "local_folder": local_folder or str(folder_path.relative_to(PROJECT_ROOT)),
                })

        status = f"  {pl['schema_subgenre']:<30}  {len(pl['tracks'])} tracks,  {missing_in_playlist} missing"
        print(status)

    delta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_missing": len(delta_tracks),
        "tracks": delta_tracks,
    }
    DELTA_PATH.write_text(json.dumps(delta, indent=2, ensure_ascii=False) + "\n")

    print(f"\n---")
    print(f"Checked:  {total_checked} Spotify tracks")
    print(f"Missing:  {len(delta_tracks)} tracks → delta.json")


if __name__ == "__main__":
    main()
