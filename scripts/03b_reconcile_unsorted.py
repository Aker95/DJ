#!/usr/bin/env python3
"""
03b_reconcile_unsorted.py

Scans ALL local folders under MUSIC_ROOT that are NOT schema-aligned.
Tries to fuzzy-match each audio file against delta tracks.

On match:
  - Moves the file to the correct schema folder.
  - Removes the track from delta.json (updates in-place).

Filename preprocessing strips common noise:
  - Track number prefixes:   "01-", "02 ", "03_"
  - Tool/site prefixes:      "SpotiDownloader.com - ", "Beatport - "
  - Mix/version suffixes:    "(Original Mix)", "[Extended Edit]", etc.
  - Underscores → spaces

Match thresholds (separate per field):
  - artist >= 0.80
  - title  >= 0.85

Run: python scripts/03b_reconcile_unsorted.py
"""

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.config import load_env, load_schema, PROJECT_ROOT
from utils.schema import canonical_key, iter_destinations
from utils.matching import (
    normalize,
    clean,
    preprocess_stem,
    match_scores,
    artist_found_in_stem,
)

DELTA_PATH = PROJECT_ROOT / "delta.json"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a"}


def get_schema_folders(schema: dict) -> set[Path]:
    """Return resolved absolute paths of all schema-defined local folders."""
    return {
        destination["local_folder"].resolve()
        for destination in iter_destinations(schema, project_root=PROJECT_ROOT)
        if destination.get("local_folder")
    }


def build_subgenre_folder_index(schema: dict) -> dict[str, Path]:
    """
    Return {normalized_subgenre_name: local_folder_path} from the live schema.
    Used so 03b always resolves destination from the schema rather than the
    potentially-stale local_folder stored in delta.json / the snapshot.
    """
    return {
        canonical_key(destination["name"]): destination["local_folder"]
        for destination in iter_destinations(schema, project_root=PROJECT_ROOT)
        if destination.get("local_folder")
    }


def main():
    env = load_env()
    schema = load_schema()
    music_root = Path(env["MUSIC_ROOT"])

    if not DELTA_PATH.exists():
        print("ERROR: delta.json not found. Run 03_delta_scan.py first.")
        sys.exit(1)

    delta = json.loads(DELTA_PATH.read_text())
    original_count = delta["total_missing"]
    remaining = list(delta["tracks"])

    schema_folders = get_schema_folders(schema)
    subgenre_folder_index = build_subgenre_folder_index(schema)

    # All direct subdirs of music_root that are NOT schema-aligned
    unsorted_dirs = [
        d for d in music_root.iterdir()
        if d.is_dir() and d.resolve() not in schema_folders
    ]

    if not unsorted_dirs:
        print("No unsorted directories found. Nothing to reconcile.")
        return

    dir_names = ", ".join(d.name for d in unsorted_dirs)
    print(f"Scanning {len(unsorted_dirs)} unsorted folder(s): {dir_names}\n")

    reconciled = 0
    still_remaining = []

    for track in remaining:
        found = False
        for d in unsorted_dirs:
            for f in sorted(d.iterdir()):
                if not f.is_file() or f.suffix.lower() not in AUDIO_EXTENSIONS:
                    continue

                cand_artist, cand_title = preprocess_stem(f.stem)
                a_score, t_score = match_scores(cand_artist, cand_title, track["artist"], track["title"])

                stage1 = a_score >= 0.80 and t_score >= 0.85
                stage2 = (not stage1) and t_score >= 0.95 and artist_found_in_stem(track["artist"], f.stem)

                if stage1 or stage2:
                    # Resolve destination from live schema (never trust stale delta paths)
                    sg_key = canonical_key(
                        track.get("subgenre") or track.get("main_genre", "")
                    )
                    dest_folder = subgenre_folder_index.get(sg_key)
                    if dest_folder is None:
                        # Fallback: use whatever is in the delta (may be stale)
                        dest_folder = PROJECT_ROOT / track["local_folder"]
                    dest_folder.mkdir(parents=True, exist_ok=True)
                    dest = dest_folder / f.name
                    shutil.move(str(f), str(dest))

                    stage_label = "standard" if stage1 else "artist-in-stem fallback"
                    print(f"[FOUND]  {d.name}/{f.name}")
                    print(f"         → matched: {track['artist']} - {track['title']}")
                    print(f"         → scores:  artist {a_score:.2f}  title {t_score:.2f}  [{stage_label}]")
                    print(f"         → moved to: {dest_folder.relative_to(PROJECT_ROOT)}/\n")

                    reconciled += 1
                    found = True
                    break

            if found:
                break

        if not found:
            still_remaining.append(track)

    # Update delta.json in-place
    delta["tracks"] = still_remaining
    delta["total_missing"] = len(still_remaining)
    delta["reconciled_from_unsorted"] = reconciled
    delta["last_reconciled_at"] = datetime.now(timezone.utc).isoformat()
    DELTA_PATH.write_text(json.dumps(delta, indent=2, ensure_ascii=False) + "\n")

    print(f"---")
    print(f"Reconciled:  {reconciled} track(s) moved from unsorted folders")
    print(f"Delta:       {original_count} → {len(still_remaining)} remaining")
    if still_remaining:
        print(f"Next step:   python scripts/04_acquire.py")


if __name__ == "__main__":
    main()
