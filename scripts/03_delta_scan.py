#!/usr/bin/env python3
"""
03_delta_scan.py

Compares tracks in spotify_snapshot.json against local schema-aligned folders.
Writes delta.json with tracks that are on Spotify but missing locally.

Matching uses the same two-stage rapidfuzz pipeline as 03b_reconcile_unsorted:
  Stage 1: artist token_set_ratio >= 0.80 AND title token_set_ratio >= 0.85
  Stage 2: title token_set_ratio >= 0.95 AND artist found as substring in stem
Filename preprocessing strips track-number prefixes, site prefixes (e.g.
SpotiDownloader), and mix/version suffixes before comparing.

Run: python scripts/03_delta_scan.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.config import load_env, load_schema, PROJECT_ROOT
from utils.matching import (
    normalize,
    clean,
    preprocess_stem,
    match_scores,
    artist_found_in_stem,
)

SNAPSHOT_PATH = PROJECT_ROOT / "spotify_snapshot.json"
DELTA_PATH = PROJECT_ROOT / "delta.json"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a"}


def build_subgenre_folder_index(schema: dict) -> dict[str, Path]:
    """Return {normalized_subgenre_name: local_folder_path} from the live schema."""
    index = {}
    for folder in schema["master_folders"]:
        for sg in folder["subgenres"]:
            lf = sg.get("local_folder")
            if lf:
                index[sg["name"].strip().lower()] = PROJECT_ROOT / lf
    return index


def load_local_stems(folder_path: Path) -> list[str]:
    """Return raw (un-normalized) filename stems for all audio files in folder."""
    if not folder_path.exists():
        return []
    return [
        f.stem
        for f in folder_path.iterdir()
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    ]


def is_local_match(local_stems: list[str], artist: str, title: str) -> bool:
    """Two-stage match: same thresholds as 03b_reconcile_unsorted."""
    for stem in local_stems:
        cand_artist, cand_title = preprocess_stem(stem)
        a_score, t_score = match_scores(cand_artist, cand_title, artist, title)
        stage1 = a_score >= 0.80 and t_score >= 0.85
        stage2 = (not stage1) and t_score >= 0.95 and artist_found_in_stem(artist, stem)
        if stage1 or stage2:
            return True
    return False


def main():
    env = load_env()
    schema = load_schema()
    music_root = Path(env["MUSIC_ROOT"])

    if not SNAPSHOT_PATH.exists():
        print("ERROR: spotify_snapshot.json not found. Run 01_spotify_snapshot.py first.")
        sys.exit(1)

    subgenre_folder_index = build_subgenre_folder_index(schema)
    snapshot = json.loads(SNAPSHOT_PATH.read_text())
    delta_tracks = []
    total_checked = 0

    for pl in snapshot["playlists"]:
        sg_key = pl["schema_subgenre"].strip().lower()
        folder_path = subgenre_folder_index.get(sg_key)
        if folder_path is None:
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
                    "local_folder": str(folder_path.relative_to(PROJECT_ROOT)),
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
