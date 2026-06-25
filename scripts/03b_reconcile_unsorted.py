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
import re
import shutil
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.config import load_env, load_schema, PROJECT_ROOT

DELTA_PATH = PROJECT_ROOT / "delta.json"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a"}

# Preprocessing regexes
RE_TRACK_NUM = re.compile(r"^[\d]{1,3}[-_\s]+")
RE_SITE_PREFIX = re.compile(r"^[A-Za-z0-9]+\.[a-zA-Z]{2,4}\s*-\s*")
RE_MIX_SUFFIX = re.compile(
    r"\s*[\(\[][^\)\]]*\b(mix|edit|remix|dub|vip|radio|original|extended|club|version|rework|cut|instrumental)\b[^\)\]]*[\)\]]",
    re.IGNORECASE,
)


def preprocess_stem(stem: str) -> tuple[str, str]:
    """
    Clean a filename stem and return (candidate_artist, candidate_title).
    Returns ("", cleaned_stem) if no " - " separator is found.
 
    SpotiDownloader (and similar tools) name files as:
      'Title - Artist'  or  'Title - Remix Info - Artist'
    so when a site prefix is detected the artist is the LAST dash-segment
    and we use rsplit to extract it correctly.
    """
    s = stem.replace("_", " ")
    had_site_prefix = bool(RE_SITE_PREFIX.match(s))
    s = RE_SITE_PREFIX.sub("", s)
    s = RE_TRACK_NUM.sub("", s)
    s = RE_MIX_SUFFIX.sub("", s)
    s = s.strip(" -")

    if " - " not in s:
        return "", s.strip()

    if had_site_prefix:
        # Artist is always the last segment in SpotiDownloader-style names
        title, artist = s.rsplit(" - ", 1)
        return artist.strip(), title.strip()

    parts = s.split(" - ", 1)
    return parts[0].strip(), parts[1].strip()


def normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _title_score(cand: str, ref: str) -> float:
    """
    Compare two titles, also trying just the base part of `ref` (before the first
    ' - '). This handles the common case where the local file's mix suffix was
    stripped by RE_MIX_SUFFIX but the Spotify title still carries a shorter version
    of it (e.g. cand='Rise', ref='Rise - Deluxe Mix' → base='Rise' → score 1.0).
    """
    s_full = SequenceMatcher(None, normalize(cand), normalize(ref)).ratio()
    ref_base = ref.split(" - ", 1)[0] if " - " in ref else ref
    s_base = SequenceMatcher(None, normalize(cand), normalize(ref_base)).ratio()
    return max(s_full, s_base)


def match_scores(cand_artist: str, cand_title: str, delta_artist: str, delta_title: str) -> tuple[float, float]:
    """
    Return best (artist_score, title_score) trying both field orderings.
    Handles filenames where title and artist are swapped (e.g. "Title - Artist").
    """
    a1 = SequenceMatcher(None, normalize(cand_artist), normalize(delta_artist)).ratio()
    t1 = _title_score(cand_title, delta_title)
    # Try swapped order
    a2 = SequenceMatcher(None, normalize(cand_title), normalize(delta_artist)).ratio()
    t2 = _title_score(cand_artist, delta_title)
    # Return the ordering that produces the higher combined score
    if a1 + t1 >= a2 + t2:
        return a1, t1
    return a2, t2


def get_schema_folders(schema: dict) -> set[Path]:
    """Return resolved absolute paths of all schema-defined local folders."""
    folders = set()
    for folder in schema["master_folders"]:
        for sg in folder["subgenres"]:
            lf = sg.get("local_folder")
            if lf:
                folders.add((PROJECT_ROOT / lf).resolve())
    return folders


def build_subgenre_folder_index(schema: dict) -> dict[str, Path]:
    """
    Return {normalized_subgenre_name: local_folder_path} from the live schema.
    Used so 03b always resolves destination from the schema rather than the
    potentially-stale local_folder stored in delta.json / the snapshot.
    """
    index = {}
    for folder in schema["master_folders"]:
        for sg in folder["subgenres"]:
            lf = sg.get("local_folder")
            if lf:
                key = sg["name"].strip().lower()
                index[key] = PROJECT_ROOT / lf
    return index


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

                if a_score >= 0.80 and t_score >= 0.85:
                    # Resolve destination from live schema (never trust stale delta paths)
                    sg_key = track["subgenre"].strip().lower()
                    dest_folder = subgenre_folder_index.get(sg_key)
                    if dest_folder is None:
                        # Fallback: use whatever is in the delta (may be stale)
                        dest_folder = PROJECT_ROOT / track["local_folder"]
                    dest_folder.mkdir(parents=True, exist_ok=True)
                    dest = dest_folder / f.name
                    shutil.move(str(f), str(dest))

                    print(f"[FOUND]  {d.name}/{f.name}")
                    print(f"         → matched: {track['artist']} - {track['title']}")
                    print(f"         → scores:  artist {a_score:.2f}  title {t_score:.2f}")
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
