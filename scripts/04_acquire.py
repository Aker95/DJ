#!/usr/bin/env python3
"""
04_acquire.py

Downloads tracks listed in delta.json via slskd (Soulseek), with yt-dlp fallback.

Requires explicit 'y' confirmation before starting any downloads.

Quality gate:
  - MP3: minimum MIN_BITRATE_KBPS (default 320)
  - Prefers FLAC > WAV > AIFF > MP3 (PREFERRED_FORMATS in .env)

Run: python scripts/04_acquire.py
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

from utils.config import load_env, PROJECT_ROOT

DELTA_PATH = PROJECT_ROOT / "delta.json"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a"}
SLSKD_DOWNLOADS_DIR = Path.home() / ".slskd" / "downloads"


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
    return responses.json() if responses.ok else []


def pick_best_file(results: list, min_bitrate_kbps: int, preferred_formats: list) -> tuple[str | None, str | None]:
    """Return (username, remote_filename) of the best quality file found."""
    candidates = []
    for result in results:
        username = result.get("username", "")
        for f in result.get("files", []):
            filename = f.get("filename", "")
            ext = Path(filename).suffix.lower().lstrip(".")
            if ext not in preferred_formats:
                continue
            bitrate = f.get("bitRate") or 0
            size = f.get("size") or 0
            if ext == "mp3" and bitrate < min_bitrate_kbps:
                continue
            priority = preferred_formats.index(ext) if ext in preferred_formats else 99
            candidates.append((priority, -size, username, filename))

    if not candidates:
        return None, None
    candidates.sort()
    _, _, username, filename = candidates[0]
    return username, filename


def slskd_download(base_url: str, api_key: str, username: str, filename: str) -> None:
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    resp = requests.post(
        f"{base_url}/api/v0/transfers/downloads/{username}",
        headers=headers,
        json=[{"filename": filename}],
        timeout=10,
    )
    resp.raise_for_status()


def wait_for_slskd_file(base_url: str, api_key: str, username: str, filename: str,
                         dest_folder: Path, timeout_s: int = 300) -> str | None:
    """Poll slskd downloads dir for the completed file, then move it to dest_folder."""
    headers = {"X-API-Key": api_key}
    fname = Path(filename).name
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        time.sleep(4)

        # Check API transfer state
        resp = requests.get(
            f"{base_url}/api/v0/transfers/downloads/{username}", headers=headers, timeout=5
        )
        if resp.ok:
            for t in resp.json():
                t_filename = t.get("filename", "")
                if not t_filename.endswith(fname):
                    continue
                state = t.get("state", "")
                if "Errored" in state or "Cancelled" in state:
                    return None
                if "Completed" in state and "Succeeded" in state:
                    break

        # Scan downloads directory for the file
        for candidate in SLSKD_DOWNLOADS_DIR.rglob(fname):
            if candidate.is_file() and candidate.stat().st_size > 0:
                dest_folder.mkdir(parents=True, exist_ok=True)
                dest = dest_folder / fname
                candidate.rename(dest)
                return str(dest)

    return None


# ---------------------------------------------------------------------------
# yt-dlp fallback
# ---------------------------------------------------------------------------

def ytdlp_download(artist: str, title: str, dest_folder: Path) -> bool:
    dest_folder.mkdir(parents=True, exist_ok=True)
    query = f"{artist} - {title}"
    result = subprocess.run(
        [
            "yt-dlp",
            f"ytsearch1:{query}",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "-o", str(dest_folder / "%(uploader)s - %(title)s.%(ext)s"),
            "--no-playlist",
            "--quiet",
            "--no-warnings",
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    env = load_env()

    if not DELTA_PATH.exists():
        print("ERROR: delta.json not found. Run 03_delta_scan.py (and 03b) first.")
        sys.exit(1)

    delta = json.loads(DELTA_PATH.read_text())
    tracks = delta.get("tracks", [])

    if not tracks:
        print("Delta is empty — nothing to download.")
        sys.exit(0)

    print(f"Tracks to download: {len(tracks)}\n")
    preview = tracks[:10]
    for t in preview:
        print(f"  {t['artist']} – {t['title']}  [{t['subgenre']}]")
    if len(tracks) > 10:
        print(f"  ... and {len(tracks) - 10} more")

    confirm = input(f"\nDownload {len(tracks)} tracks? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    base_url = env["SLSKD_BASE_URL"]
    api_key = env["SLSKD_API_KEY"]
    min_bitrate = env["MIN_BITRATE_KBPS"]
    preferred_formats = env["PREFERRED_FORMATS"]

    downloaded, failed = 0, 0

    for i, track in enumerate(tracks, 1):
        artist = track["artist"]
        title = track["title"]
        local_folder = PROJECT_ROOT / track["local_folder"]
        local_folder.mkdir(parents=True, exist_ok=True)

        print(f"\n[{i}/{len(tracks)}]  {artist} – {title}")

        # --- Primary: slskd ---
        slskd_ok = False
        try:
            results = slskd_search(base_url, api_key, f"{artist} {title}")
            username, filename = pick_best_file(results, min_bitrate, preferred_formats)
            if username and filename:
                slskd_download(base_url, api_key, username, filename)
                dest = wait_for_slskd_file(base_url, api_key, username, filename, local_folder)
                if dest:
                    print(f"  ✓  slskd  →  {Path(dest).name}")
                    downloaded += 1
                    slskd_ok = True
            else:
                print(f"  slskd: no quality match found")
        except Exception as e:
            print(f"  slskd error: {e}")

        if slskd_ok:
            continue

        # --- Fallback: yt-dlp ---
        print(f"  → yt-dlp fallback")
        ok = ytdlp_download(artist, title, local_folder)
        if ok:
            print(f"  ✓  yt-dlp")
            downloaded += 1
        else:
            print(f"  ✗  failed")
            failed += 1

    print(f"\n---")
    print(f"Downloaded: {downloaded}   Failed: {failed}   Total: {len(tracks)}")


if __name__ == "__main__":
    main()
