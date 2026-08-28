#!/usr/bin/env python3
"""Write a read-only snapshot of schema-mapped Spotify playlists."""

from __future__ import annotations

import argparse
from collections import defaultdict

from utils.config import load_config
from utils.io import now_iso, write_json
from utils.schema import canonical_key, iter_destinations, load_schema
from utils.spotify import READ_SCOPE, fetch_playlist_tracks, fetch_playlists, get_client


def destination_indexes(schema: dict) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    by_id: dict[str, list[dict]] = defaultdict(list)
    by_name: dict[str, list[dict]] = defaultdict(list)
    for destination in iter_destinations(schema):
        if destination.get("spotify_playlist"):
            by_id[destination["spotify_playlist"]].append(destination)
        by_name[canonical_key(destination["spotify_playlist_name"])].append(destination)
    return by_id, by_name


def match_destination(playlist: dict, indexes: tuple[dict, dict]) -> dict | None:
    by_id, by_name = indexes
    matches = by_id.get(playlist["id"], [])
    if len(matches) == 1:
        return matches[0]
    named = by_name.get(canonical_key(playlist.get("name", "")), [])
    return named[0] if len(named) == 1 else None


def build_snapshot(spotify, schema: dict) -> dict:
    playlists: list[dict] = []
    inaccessible: list[str] = []
    skipped_bags: list[str] = []
    indexes = destination_indexes(schema)
    for playlist in fetch_playlists(spotify):
        name = (playlist.get("name") or "").strip()
        name_words = set(canonical_key(name).split())
        if name_words & {"bag", "staging"}:
            skipped_bags.append(name)
            continue
        destination = match_destination(playlist, indexes)
        if destination is None:
            continue
        tracks = fetch_playlist_tracks(spotify, playlist["id"], rich=True)
        if tracks is None:
            inaccessible.append(playlist["id"])
            continue
        playlists.append(
            {
                "spotify_playlist_id": playlist["id"],
                "spotify_playlist_name": name,
                "placement_id": destination["placement_id"],
                "master_folder": destination["master_folder"],
                "main_genre": destination["main_genre"],
                "subgenre": destination["subgenre"],
                "local_folder": destination["local_folder"],
                "tracks": tracks,
            }
        )
    return {
        "generated_at": now_iso(),
        "playlists": playlists,
        "summary": {
            "playlists": len(playlists),
            "tracks": sum(len(item["tracks"]) for item in playlists),
            "skipped_bags": skipped_bags,
            "inaccessible": inaccessible,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Save a read-only snapshot of schema-mapped Spotify playlists."
    )
    parser.parse_args()
    config = load_config()
    snapshot = build_snapshot(
        get_client(config, scope=READ_SCOPE), load_schema(config.schema_path)
    )
    write_json(config.data_dir / "spotify_snapshot.json", snapshot)
    print(
        f"Snapshot saved: {snapshot['summary']['playlists']} playlists, "
        f"{snapshot['summary']['tracks']} tracks."
    )


if __name__ == "__main__":
    main()
