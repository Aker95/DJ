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
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).parent))

from utils.config import load_env, load_schema, PROJECT_ROOT
from utils.schema import canonical_key, iter_destinations
from utils.matching import filename_matches, sig_words

DELTA_PATH = PROJECT_ROOT / "delta.json"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".opus"}
SLSKD_DOWNLOADS_DIR = Path.home() / ".slskd" / "downloads"
PARALLEL_WORKERS = 3  # concurrent downloads
_SEARCH_LOCK = threading.Lock()  # one slskd search at a time to avoid 429s
SLSKD_CONTAINER_NAME = "slskd"


# ---------------------------------------------------------------------------
# slskd helpers
# ---------------------------------------------------------------------------

def ensure_slskd_available(
    base_url: str,
    api_key: str,
    retries: int = 15,
    retry_delay_s: float = 1.0,
) -> None:
    """Ensure the slskd API is ready, starting its local Docker container if needed."""
    headers = {"X-API-Key": api_key or ""}
    session_url = f"{base_url.rstrip('/')}/api/v0/session"

    def check() -> None:
        response = requests.get(session_url, headers=headers, timeout=3)
        response.raise_for_status()

    try:
        check()
        return
    except requests.RequestException as initial_error:
        hostname = (urlparse(base_url).hostname or "").lower()
        if hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError(
                f"slskd is unavailable at {base_url}: {initial_error}"
            ) from initial_error

    try:
        result = subprocess.run(
            ["docker", "start", SLSKD_CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            f"slskd is unavailable and Docker could not start {SLSKD_CONTAINER_NAME!r}: {error}"
        ) from error

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Docker error"
        raise RuntimeError(
            f"slskd is unavailable and `docker start {SLSKD_CONTAINER_NAME}` failed: {detail}"
        )

    last_error: requests.RequestException | None = None
    for _ in range(retries):
        try:
            check()
            print(f"Started Docker container {SLSKD_CONTAINER_NAME!r}; API is ready.")
            return
        except requests.RequestException as error:
            last_error = error
            time.sleep(retry_delay_s)

    raise RuntimeError(
        f"Docker started {SLSKD_CONTAINER_NAME!r}, but slskd did not become ready at "
        f"{base_url}: {last_error}"
    )

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


def pick_best_file(
    results: list, min_bitrate_kbps: int, preferred_formats: list,
    artist: str = "", title: str = "",
    max_candidates: int = 5,
) -> list[tuple[str, str, int]]:
    """Return up to max_candidates ranked (username, filename, size) tuples.

    Sorted best-first: format priority → extended mix → largest size.
    Files are pre-filtered by a filename quality gate (filename_matches).
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
            if artist and title and not filename_matches(filename, artist, title):
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
    return {
        canonical_key(destination["name"]): destination["local_folder"]
        for destination in iter_destinations(schema, project_root=PROJECT_ROOT)
        if destination.get("local_folder")
    }


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
    sg_key = canonical_key(track.get("subgenre") or track.get("main_genre", ""))
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

    try:
        ensure_slskd_available(env["SLSKD_BASE_URL"], env["SLSKD_API_KEY"])
    except RuntimeError as error:
        print(f"ERROR: {error}")
        sys.exit(1)

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
