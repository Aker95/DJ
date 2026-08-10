#!/usr/bin/env python3
"""
01_spotify_snapshot.py

Reads all user Spotify playlists, matches them against schema subgenres,
fetches all tracks, and writes spotify_snapshot.json.

Rules:
  - Skips any playlist whose name ends with 'bag' (case-insensitive staging playlists).
  - Only processes playlists whose name exactly matches a schema subgenre (case-insensitive).
  - Back-fills spotify_playlist IDs into music_schema_knowledge.json where null.

Run: python scripts/01_spotify_snapshot.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from spotipy.exceptions import SpotifyException

sys.path.insert(0, str(Path(__file__).parent))

from utils.config import load_env, load_schema, save_schema, PROJECT_ROOT
from utils.schema import canonical_key, is_v2, iter_destinations
from utils.spotify_auth import get_spotify_client, clear_token_cache

SNAPSHOT_PATH = PROJECT_ROOT / "spotify_snapshot.json"


def fetch_all_playlists(sp) -> list:
    playlists = []
    results = sp.current_user_playlists(limit=50)
    while results:
        playlists.extend(results["items"])
        results = sp.next(results) if results.get("next") else None
    return playlists


def fetch_all_tracks(sp, playlist_id: str) -> list | None:
    """Returns list of tracks, or None if the playlist is inaccessible (403)."""
    tracks = []
    try:
        results = sp.playlist_items(
            playlist_id,
            fields="items(item(id,name,artists,duration_ms)),next",
            additional_types=["track"],
        )
    except SpotifyException as e:
        if e.http_status == 403:
            return None
        raise
    while results:
        for item in results.get("items", []):
            t = item.get("item")
            if not t or not t.get("id"):
                continue
            tracks.append({
                "id": t["id"],
                "title": t["name"],
                "artist": t["artists"][0]["name"] if t.get("artists") else "",
                "duration_ms": t.get("duration_ms"),
            })
        results = sp.next(results) if results.get("next") else None
    return tracks


def build_subgenre_index(schema: dict) -> dict:
    """Returns {normalized_name: (master_folder_name, subgenre_dict)}."""
    index = {}
    if is_v2(schema):
        for destination in iter_destinations(schema, project_root=PROJECT_ROOT):
            node = destination["playlist_node"]
            index[canonical_key(destination["name"])] = (
                destination["master_folder"], node
            )
        return index
    for folder in schema["master_folders"]:
        for sg in folder["subgenres"]:
            key = sg["name"].strip().lower()
            index[key] = (folder["name"], sg)
    return index


def build_destination_index(schema: dict) -> dict:
    """Return normalized playlist-name routing records for schema v1 or v2."""
    return {
        canonical_key(destination["name"]): destination
        for destination in iter_destinations(schema, project_root=PROJECT_ROOT)
    }


def main():
    env = load_env()
    schema = load_schema()
    sp = get_spotify_client(scope="playlist-read-private playlist-read-collaborative")

    print("Fetching user playlists...")
    all_playlists = fetch_all_playlists(sp)
    print(f"  Found {len(all_playlists)} playlists total.\n")

    destination_index = build_destination_index(schema)
    schema_modified = False
    snapshot_playlists = []
    skipped_bag = []

    for pl in all_playlists:
        name = (pl.get("name") or "").strip()
        pl_id = pl["id"]

        # Skip bag playlists (staging/split-queue folders)
        if name.lower().endswith("bag"):
            skipped_bag.append(name)
            continue

        norm_name = canonical_key(name)
        if norm_name not in destination_index:
            continue

        destination = destination_index[norm_name]
        master_folder_name = destination["master_folder"]
        playlist_node = destination["playlist_node"]
        print(f"  [MATCH]  {name}  →  {master_folder_name}")

        # Back-fill playlist ID into schema
        if playlist_node.get("spotify_playlist") is None:
            playlist_node["spotify_playlist"] = pl_id
            schema_modified = True

        tracks = fetch_all_tracks(sp, pl_id)
        if tracks is None:
            print(f"           [SKIP] playlist inaccessible (403), skipping.")
            continue
        print(f"           {len(tracks)} tracks")

        snapshot_playlists.append({
            "playlist_id": pl_id,
            "playlist_name": name,
            "schema_subgenre": destination["name"],
            "classification_level": destination["classification_level"],
            "main_genre": destination.get("main_genre"),
            "subgenre": destination.get("subgenre"),
            "master_folder": master_folder_name,
            "local_folder": (
                str(destination["local_folder"].relative_to(PROJECT_ROOT))
                if destination.get("local_folder")
                else None
            ),
            "tracks": tracks,
        })

    if skipped_bag:
        print(f"\n  [SKIP bag]  {len(skipped_bag)} playlist(s): {', '.join(skipped_bag)}")

    if schema_modified:
        save_schema(schema)
        print("\nUpdated music_schema_knowledge.json with playlist IDs.")

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_playlists": len(snapshot_playlists),
        "playlists": snapshot_playlists,
    }
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n")

    total_tracks = sum(len(p["tracks"]) for p in snapshot_playlists)
    print(f"\nWrote {SNAPSHOT_PATH.name}  ({len(snapshot_playlists)} playlists, {total_tracks} tracks total)")

    clear_token_cache()
    print("Spotify token cache cleared.")


if __name__ == "__main__":
    main()
