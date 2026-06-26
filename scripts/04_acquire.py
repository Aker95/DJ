#!/usr/bin/env python3
"""
04_acquire.py

Downloads tracks listed in delta.json via slskd (Soulseek).

Requires explicit 'y' confirmation before starting any downloads.

Quality gate:
  - MP3: minimum MIN_BITRATE_KBPS (default 320)
  - Prefers FLAC > WAV > AIFF > MP3 (PREFERRED_FORMATS in .env)

Destination folders are resolved from the live schema (same source as 03_delta_scan).

Run: python scripts/04_acquire.py
"""

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import re
import unicodedata

import requests
from rapidfuzz import fuzz as rf

sys.path.insert(0, str(Path(__file__).parent))

from utils.config import load_env, load_schema, PROJECT_ROOT

DELTA_PATH = PROJECT_ROOT / "delta.json"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".opus"}
SLSKD_DOWNLOADS_DIR = Path.home() / ".slskd" / "downloads"
PARALLEL_WORKERS = 3  # concurrent downloads
_SEARCH_LOCK = threading.Lock()  # one slskd search at a time to avoid 429s


# ---------------------------------------------------------------------------
# slskd helpers
# ---------------------------------------------------------------------------

def slskd_search(base_url: str, api_key: str, query: str, timeout_s: int = 30) -> list:
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    resp = requests.post(
        f"{base_url}/api/v0/searches",
        headers=headers,
        json={"searchText": query, "fileLimit": 200},
        timeout=10,
    )
    resp.raise_for_status()
    search_id = resp.json()["id"]

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = requests.get(
            f"{base_url}/api/v0/searches/{search_id}", headers=headers, timeout=5
        ).json()
        if status.get("isComplete"):
            break
        time.sleep(2)

    responses = requests.get(
        f"{base_url}/api/v0/searches/{search_id}/responses", headers=headers, timeout=5
    )
    if not responses.ok:
        return []
    data = responses.json()
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# Filename quality gate (same two-stage pipeline as 03_delta_scan)
# ---------------------------------------------------------------------------

_RE_MIX_SUFFIX = re.compile(r"\s*[-–]\s*(original|extended|club|radio|vocal|instrumental|remaster\w*)\s*(mix|edit|version|remix)?\s*$", re.I)
_RE_FEAT = re.compile(r"\s*(feat\.?|ft\.?|featuring)\s+[^(]+", re.I)
_RE_TRACK_NUM = re.compile(r"^\d+[.\s]+")
_RE_SITE_PREFIX = re.compile(r"^SpotiDownloader\.com\s*-\s*", re.I)


def _normalize(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _clean(s: str) -> str:
    s = _RE_MIX_SUFFIX.sub("", s)
    s = _RE_FEAT.sub("", s)
    return _normalize(s).strip()


def _preprocess_stem(stem: str) -> tuple[str, str]:
    s = stem.replace("_", " ")
    had_site = bool(_RE_SITE_PREFIX.match(s))
    s = _RE_SITE_PREFIX.sub("", s)
    s = _RE_TRACK_NUM.sub("", s)
    s = _RE_MIX_SUFFIX.sub("", s)
    s = s.strip(" -")
    if " - " not in s:
        return "", s.strip()
    if had_site:
        title, artist = s.rsplit(" - ", 1)
        return artist.strip(), title.strip()
    parts = s.split(" - ", 1)
    return parts[0].strip(), parts[1].strip()


_STOPWORDS = {"the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "ft", "feat"}


def _sig_words(s: str) -> list[str]:
    """Return significant (non-stopword, length > 2) normalised words."""
    return [w for w in _normalize(s).split() if len(w) > 2 and w not in _STOPWORDS]


def _filename_matches(filename: str, artist: str, title: str) -> bool:
    """Stage 0 (regex) → Stage 1 (fuzzy) → Stage 2 (fuzzy+substring) quality check."""
    raw_stem = filename.replace("\\", "/").split("/")[-1]
    raw_stem = Path(raw_stem).stem
    norm_stem = _normalize(raw_stem.replace("_", " "))

    # --- Stage 0: fast regex — all significant title words present as whole words ---
    title_words = _sig_words(title)
    artist_words = _sig_words(artist)
    if title_words and all(re.search(r"\b" + re.escape(w) + r"\b", norm_stem) for w in title_words):
        # At least one artist word must also appear (avoids title-only collisions)
        if not artist_words or any(re.search(r"\b" + re.escape(w) + r"\b", norm_stem) for w in artist_words):
            return True

    # --- Stage 1 & 2: fuzzy token-set-ratio ---
    cand_artist, cand_title = _preprocess_stem(raw_stem)
    ca, ra = _clean(cand_artist), _clean(artist)
    ct, rt = _clean(cand_title), _clean(title)
    a1 = rf.token_set_ratio(ca, ra) / 100
    t1 = rf.token_set_ratio(ct, rt) / 100
    a2 = rf.token_set_ratio(ct, ra) / 100
    t2 = rf.token_set_ratio(ca, rt) / 100
    a_score, t_score = (a1, t1) if a1 + t1 >= a2 + t2 else (a2, t2)
    stage1 = a_score >= 0.80 and t_score >= 0.85
    artist_in_stem = bool(ra) and ra in norm_stem
    stage2 = (not stage1) and t_score >= 0.95 and artist_in_stem
    return stage1 or stage2


def pick_best_file(
    results: list, min_bitrate_kbps: int, preferred_formats: list,
    artist: str = "", title: str = "",
    max_candidates: int = 3,
) -> list[tuple[str, str, int]]:
    """Return up to max_candidates ranked (username, filename, size) tuples.

    Sorted best-first: format priority → extended mix → largest size.
    Files are pre-filtered by a two-stage fuzzy filename quality gate.
    """
    candidates = []
    for result in results:
        if not isinstance(result, dict):
            continue
        username = result.get("username", "")
        for f in result.get("files", []):
            if not isinstance(f, dict):
                continue
            filename = f.get("filename", "")
            ext = Path(filename).suffix.lower().lstrip(".")
            if not ext:
                ext = (f.get("extension") or "").lower().lstrip(".")
            if ext not in preferred_formats:
                continue
            size = f.get("size") or 0
            length = f.get("length") or 0
            estimated_bitrate = int((size * 8) / (length * 1000)) if length > 0 else 0
            if ext == "mp3" and estimated_bitrate < min_bitrate_kbps:
                continue
            # Quality gate: filename must match the target artist/title
            if artist and title and not _filename_matches(filename, artist, title):
                continue
            priority = preferred_formats.index(ext) if ext in preferred_formats else 99
            not_extended = 0 if "extended" in filename.lower() else 1
            candidates.append((priority, not_extended, -size, username, filename))

    candidates.sort()
    return [(u, fn, -sz) for _, _, sz, u, fn in candidates[:max_candidates]]


def slskd_download(base_url: str, api_key: str, username: str, filename: str, size: int = 0) -> None:
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    payload = [{"filename": filename}]
    if size > 0:
        payload[0]["size"] = size
    resp = requests.post(
        f"{base_url}/api/v0/transfers/downloads/{username}",
        headers=headers,
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()


def wait_for_slskd_file(
    base_url: str, api_key: str, username: str, filename: str,
    dest_folder: Path,
    queue_timeout_s: int = 300,   # max wait while bytes == 0 (peer still in queue)
    stall_timeout_s: int = 300,   # max wait after bytes start moving but then freeze
) -> str | None:
    """Poll slskd for a completed download then move it to dest_folder.

    Two independent timeouts:
    - queue_timeout_s: peer hasn't sent a single byte yet (transfer queued)
    - stall_timeout_s: transfer started but bytes froze mid-way
    """
    headers = {"X-API-Key": api_key}
    # slskd returns Windows-style backslash paths; Path.name won't split on \ on macOS
    fname = filename.replace("\\", "/").split("/")[-1]
    # Overall ceiling keeps the function bounded even if clocks drift
    deadline = time.time() + queue_timeout_s + stall_timeout_s

    queue_start: float = time.time()   # clock for queue_timeout
    stall_start: float | None = None   # clock for stall_timeout (starts once bytes > 0)
    last_bytes: int = 0

    while time.time() < deadline:
        time.sleep(4)

        transfer_failed = False
        transfer_done = False

        try:
            resp = requests.get(
                f"{base_url}/api/v0/transfers/downloads/{username}", headers=headers, timeout=5
            )
        except requests.RequestException:
            continue

        if resp.ok:
            data = resp.json()
            directories = data.get("directories", []) if isinstance(data, dict) else data
            for d in directories:
                if not isinstance(d, dict):
                    continue
                for t in d.get("files", []):
                    if not isinstance(t, dict):
                        continue
                    if not t.get("filename", "").endswith(fname):
                        continue
                    state = t.get("state", "")
                    if "Errored" in state or "Cancelled" in state or "Aborted" in state:
                        transfer_failed = True
                        break
                    if "Completed" in state and "Succeeded" in state:
                        transfer_done = True
                        break

                    bytes_now = t.get("bytesTransferred", 0) or 0

                    if bytes_now == 0:
                        # Still queued — check queue timeout
                        if time.time() - queue_start > queue_timeout_s:
                            transfer_failed = True
                            break
                    else:
                        # Transfer has started
                        if bytes_now > last_bytes:
                            last_bytes = bytes_now
                            stall_start = time.time()  # reset stall clock on progress
                        elif stall_start is not None:
                            if time.time() - stall_start > stall_timeout_s:
                                transfer_failed = True
                                break
                        else:
                            stall_start = time.time()  # first time we see bytes > 0
                if transfer_failed or transfer_done:
                    break

        if transfer_failed:
            return None

        # Scan downloads directory for the completed file
        for candidate in SLSKD_DOWNLOADS_DIR.rglob(fname):
            if candidate.is_file() and candidate.stat().st_size > 0:
                dest_folder.mkdir(parents=True, exist_ok=True)
                dest = dest_folder / fname
                candidate.rename(dest)
                return str(dest)

        if transfer_done:
            break

    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_subgenre_folder_index(schema: dict) -> dict[str, Path]:
    """Return {normalized_subgenre_name: local_folder_path} from the live schema."""
    index = {}
    for folder in schema["master_folders"]:
        for sg in folder["subgenres"]:
            lf = sg.get("local_folder")
            if lf:
                index[sg["name"].strip().lower()] = PROJECT_ROOT / lf
    return index


def already_downloaded(local_folder: Path, artist: str, title: str) -> bool:
    """Return True if an audio file matching this track already exists locally."""
    artist_l = artist.lower()
    title_l = title.lower()
    for f in local_folder.iterdir() if local_folder.exists() else []:
        if f.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        stem = f.stem.lower()
        if artist_l in stem and title_l in stem:
            return True
    return False


def process_track(i: int, total: int, track: dict, env: dict,
                  subgenre_index: dict, lock: threading.Lock, counters: dict) -> None:
    """Download a single track (runs in a thread worker)."""
    artist = track["artist"]
    title = track["title"]
    sg_key = track.get("subgenre", "").strip().lower()
    local_folder = subgenre_index.get(sg_key) or PROJECT_ROOT / track["local_folder"]
    local_folder.mkdir(parents=True, exist_ok=True)

    base_url = env["SLSKD_BASE_URL"]
    api_key = env["SLSKD_API_KEY"]
    min_bitrate = env["MIN_BITRATE_KBPS"]
    preferred_formats = env["PREFERRED_FORMATS"]

    def log(msg: str) -> None:
        with lock:
            print(f"[{i}/{total}]  {artist} – {title}  {msg}", flush=True)

    log("")

    if already_downloaded(local_folder, artist, title):
        log("↷  already exists, skipping")
        with lock:
            counters["skipped"] += 1
        return

    # --- Primary: slskd — try up to 3 ranked candidates before giving up ---
    slskd_ok = False
    candidates = []
    try:
        with _SEARCH_LOCK:
            results = slskd_search(base_url, api_key, f"{artist} {title}")
            time.sleep(1)
        candidates = pick_best_file(results, min_bitrate, preferred_formats, artist=artist, title=title)
    except Exception as e:
        log(f"slskd search error: {e}")

    if not candidates:
        log("slskd: no quality match found")
    else:
        for attempt, (username, filename, file_size) in enumerate(candidates, 1):
            try:
                slskd_download(base_url, api_key, username, filename, file_size)
                dest = wait_for_slskd_file(base_url, api_key, username, filename, local_folder)
                if dest:
                    log(f"✓  slskd [{attempt}/{len(candidates)}]  →  {Path(dest).name}")
                    with lock:
                        counters["downloaded"] += 1
                    slskd_ok = True
                    break
                else:
                    log(f"slskd [{attempt}/{len(candidates)}]: timed out / stalled — "
                        f"{'trying next' if attempt < len(candidates) else 'no more candidates'}")
            except Exception as e:
                log(f"slskd [{attempt}/{len(candidates)}] error: {e}")

    if not slskd_ok:
        log("✗  not found on Soulseek")
        with lock:
            counters["failed"] += 1


def main():
    env = load_env()
    schema = load_schema()
    subgenre_index = build_subgenre_folder_index(schema)
    sys.stdout.reconfigure(line_buffering=True)

    if not DELTA_PATH.exists():
        print("ERROR: delta.json not found. Run 03_delta_scan.py (and 03b) first.")
        sys.exit(1)

    delta = json.loads(DELTA_PATH.read_text())
    tracks = delta.get("tracks", [])

    if not tracks:
        print("Delta is empty — nothing to download.")
        sys.exit(0)

    print(f"Tracks to download: {len(tracks)}\n")
    for t in tracks[:10]:
        print(f"  {t['artist']} – {t['title']}  [{t['subgenre']}]")
    if len(tracks) > 10:
        print(f"  ... and {len(tracks) - 10} more")

    confirm = input(f"\nDownload {len(tracks)} tracks? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    print(f"\nRunning {PARALLEL_WORKERS} parallel workers …\n")

    lock = threading.Lock()
    counters = {"downloaded": 0, "failed": 0, "skipped": 0}

    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
        futures = {
            executor.submit(process_track, i, len(tracks), track, env, subgenre_index, lock, counters): track
            for i, track in enumerate(tracks, 1)
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                track = futures[future]
                with lock:
                    print(f"  [ERROR] {track['artist']} – {track['title']}: {e}", flush=True)

    print(f"\n---")
    print(f"Downloaded: {counters['downloaded']}   Skipped: {counters['skipped']}   Failed: {counters['failed']}   Total: {len(tracks)}")


if __name__ == "__main__":
    main()
