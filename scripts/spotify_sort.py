#!/usr/bin/env python3
"""Preview or apply approved Spotify playlist sorting."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from utils.config import load_config
from utils.io import now_iso, read_json, write_json
from utils.spotify import WRITE_SCOPE, add_tracks, get_client, playlist_track_ids, remove_tracks


def build_sort_plan(classifications: dict, remove_source: bool = True) -> dict:
    actions: list[dict] = []
    skipped: list[dict] = []
    for track in classifications.get("tracks", []):
        if not track.get("target_playlist_id"):
            skipped.append({"spotify_track_id": track.get("spotify_track_id"), "reason": "playlist-not-provisioned"})
            continue
        actions.append(
            {
                "spotify_track_id": track["spotify_track_id"],
                "artist": track.get("artist"),
                "title": track.get("title"),
                "target_playlist_id": track["target_playlist_id"],
            }
        )
    return {
        "created_at": now_iso(),
        "source_playlist_id": classifications["source_playlist_id"],
        "remove_source": remove_source,
        "tracks": actions,
        "skipped": skipped,
        "summary": {"tracks": len(actions), "skipped": len(skipped)},
    }


def apply_sort(plan: dict, spotify) -> dict:
    by_target: dict[str, list[str]] = defaultdict(list)
    for track in plan.get("tracks", []):
        by_target[track["target_playlist_id"]].append(track["spotify_track_id"])
    added = 0
    for playlist_id, track_ids in by_target.items():
        existing = playlist_track_ids(spotify, playlist_id)
        missing = [track_id for track_id in track_ids if track_id not in existing]
        add_tracks(spotify, playlist_id, missing)
        if set(track_ids) - playlist_track_ids(spotify, playlist_id):
            raise RuntimeError(f"Spotify addition verification failed for {playlist_id}")
        added += len(missing)
    all_ids = [track["spotify_track_id"] for track in plan.get("tracks", [])]
    if plan.get("remove_source"):
        removable_ids = [
            track["spotify_track_id"]
            for track in plan.get("tracks", [])
            if track["target_playlist_id"] != plan["source_playlist_id"]
        ]
        present = playlist_track_ids(spotify, plan["source_playlist_id"])
        remove_tracks(
            spotify,
            plan["source_playlist_id"],
            [track_id for track_id in removable_ids if track_id in present],
        )
        remaining = set(removable_ids) & playlist_track_ids(spotify, plan["source_playlist_id"])
        if remaining:
            raise RuntimeError(f"Tracks remain in source playlist: {sorted(remaining)}")
    return {"applied_at": now_iso(), "added": added, "satisfied": len(all_ids)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("classifications", type=Path, nargs="?", default=Path("data/classifications.json"))
    parser.add_argument("--keep-source", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    plan_path = config.data_dir / "spotify_sort_plan.json"
    if args.apply:
        result = apply_sort(read_json(plan_path), get_client(config, scope=WRITE_SCOPE))
        write_json(config.data_dir / "spotify_sort_result.json", result)
        print(f"Spotify sort verified for {result['satisfied']} track(s).")
        return
    plan = build_sort_plan(read_json(args.classifications), not args.keep_source)
    write_json(plan_path, plan)
    print(f"Planned {plan['summary']['tracks']} track(s); skipped {plan['summary']['skipped']}.")


if __name__ == "__main__":
    main()
