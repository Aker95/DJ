#!/usr/bin/env python3
"""Preview or provision one Spotify playlist for one schema destination."""

from __future__ import annotations

import argparse

from utils.config import load_config
from utils.io import write_json
from utils.schema import canonical_key, load_schema, resolve_destination, save_schema
from utils.spotify import WRITE_SCOPE, fetch_playlists, get_client


def build_playlist_plan(spotify, destination: dict) -> dict:
    playlists = fetch_playlists(spotify)
    mapped_id = destination.get("spotify_playlist")
    mapped = next((item for item in playlists if item.get("id") == mapped_id), None)
    if mapped_id and not mapped:
        raise ValueError(f"Mapped Spotify playlist {mapped_id!r} is not accessible")
    matches = [
        item
        for item in playlists
        if canonical_key(item.get("name", "")) == canonical_key(destination["spotify_playlist_name"])
    ]
    if len(matches) > 1:
        raise ValueError(f"Multiple playlists are named {destination['spotify_playlist_name']!r}")
    selected = mapped or (matches[0] if matches else None)
    return {
        "placement_id": destination["placement_id"],
        "canonical_name": destination["spotify_playlist_name"],
        "current_playlist_id": mapped_id,
        "selected_playlist_id": selected.get("id") if selected else None,
        "action": "already_mapped" if mapped else "reuse" if selected else "create",
    }


def apply_playlist_plan(spotify, destination: dict, plan: dict, public: bool) -> dict:
    if plan["action"] == "create":
        user_id = spotify.current_user()["id"]
        playlist = spotify.user_playlist_create(
            user_id,
            plan["canonical_name"],
            public=public,
            description="DJ library canonical destination.",
        )
        playlist_id = playlist["id"]
    else:
        playlist_id = plan["selected_playlist_id"]
    destination["node"]["spotify_playlist"] = playlist_id
    return {**plan, "playlist_id": playlist_id}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", required=True)
    parser.add_argument("--main", required=True)
    parser.add_argument("--subgenre")
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    schema = load_schema(config.schema_path)
    destination = resolve_destination(schema, args.master, args.main, args.subgenre)
    if destination is None:
        raise ValueError("The requested schema destination does not exist")
    spotify = get_client(config, scope=WRITE_SCOPE)
    plan = build_playlist_plan(spotify, destination)
    write_json(config.data_dir / "spotify_playlist_plan.json", plan)
    if args.apply:
        result = apply_playlist_plan(spotify, destination, plan, args.public)
        save_schema(schema, config.schema_path)
        write_json(config.data_dir / "spotify_playlist_result.json", result)
        print(f"Mapped {result['canonical_name']} to Spotify playlist {result['playlist_id']}.")
    else:
        print(f"Schema destination: {plan['placement_id']}")
        print(f"Canonical playlist name: {plan['canonical_name']}")
        print(f"Current mapped ID: {plan['current_playlist_id'] or 'none'}")
        print(f"Exact-name playlist ID: {plan['selected_playlist_id'] or 'none'}")
        print(f"Plan: {plan['action'].replace('_', ' ')}")


if __name__ == "__main__":
    main()
