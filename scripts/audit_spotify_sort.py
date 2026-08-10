#!/usr/bin/env python3
"""Read-only audit of a completed Module 06 Spotify sort."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.spotify_auth import get_spotify_client


def playlist_track_ids(sp, playlist_id: str) -> set[str]:
    ids: set[str] = set()
    page = sp.playlist_items(
        playlist_id,
        fields="items(item(id)),next",
        additional_types=["track"],
        limit=100,
    )
    while page:
        ids.update(
            item["item"]["id"]
            for item in page.get("items", [])
            if item.get("item") and item["item"].get("id")
        )
        page = sp.next(page) if page.get("next") else None
    return ids


def audit(results_path: Path) -> dict:
    results = json.loads(results_path.read_text(encoding="utf-8"))
    expected: dict[str, set[str]] = defaultdict(set)
    all_ids: set[str] = set()
    for track in results.get("tracks", []):
        if track.get("approved") is True and track.get("target_playlist_id"):
            track_id = track["spotify_track_id"]
            expected[track["target_playlist_id"]].add(track_id)
            all_ids.add(track_id)

    sp = get_spotify_client(scope="playlist-read-private playlist-read-collaborative")
    missing_by_target: dict[str, list[str]] = {}
    for playlist_id, track_ids in expected.items():
        missing = sorted(track_ids - playlist_track_ids(sp, playlist_id))
        if missing:
            missing_by_target[playlist_id] = missing

    source_id = results["source_playlist_id"]
    source_remaining = sorted(all_ids & playlist_track_ids(sp, source_id))
    return {
        "expected_tracks": len(all_ids),
        "target_playlists": len(expected),
        "missing_from_targets": missing_by_target,
        "remaining_in_source": source_remaining,
        "ok": not missing_by_target and not source_remaining,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.input)
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
