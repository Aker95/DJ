#!/usr/bin/env python3
"""Create/reuse a Spotify destination playlist and synchronize its mapping.

This uses Spotify's current ``POST /me/playlists`` endpoint because older
Spotipy releases still call the retired user-scoped creation endpoint.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.config import PROJECT_ROOT, load_schema, save_schema
from utils.schema import resolve_destination, validate_schema
from utils.spotify_auth import get_spotify_client


DEFAULT_RESULTS = PROJECT_ROOT / "classify_results.json"


def fetch_all_playlists(sp) -> list[dict]:
    playlists: list[dict] = []
    page = sp.current_user_playlists(limit=50)
    while page:
        playlists.extend(page.get("items", []))
        page = sp.next(page) if page.get("next") else None
    return playlists


def create_playlist(sp, *, name: str, description: str, public: bool) -> dict:
    # Spotify's current endpoint is POST /me/playlists. Spotipy's
    # user_playlist_create() still targets /users/{id}/playlists in the
    # installed version, which Spotify now rejects with HTTP 403.
    return sp._post(
        "me/playlists",
        payload={"name": name, "description": description, "public": public},
    )


def provision(
    *,
    playlist_name: str,
    master_folder: str,
    main_genre: str,
    subgenre: str | None,
    results_path: Path,
    description: str,
    public: bool,
) -> dict:
    schema = load_schema()
    destination = resolve_destination(
        schema,
        master_folder=master_folder,
        main_genre=main_genre,
        subgenre=subgenre,
    )
    if destination is None:
        raise ValueError(
            f"Unknown schema destination: {master_folder} / "
            f"{main_genre} / {subgenre or '(parent)'}"
        )

    sp = get_spotify_client(
        scope="playlist-read-private playlist-modify-private playlist-modify-public"
    )
    matches = [
        playlist
        for playlist in fetch_all_playlists(sp)
        if (playlist.get("name") or "").strip().casefold()
        == playlist_name.strip().casefold()
    ]
    if len(matches) > 1:
        ids = ", ".join(item["id"] for item in matches)
        raise RuntimeError(f"Multiple exact Spotify playlists named {playlist_name!r}: {ids}")

    created = not matches
    playlist = matches[0] if matches else create_playlist(
        sp,
        name=playlist_name,
        description=description,
        public=public,
    )
    playlist_id = playlist["id"]

    destination["playlist_node"]["spotify_playlist"] = playlist_id
    validate_schema(schema)
    save_schema(schema)

    updated_tracks = 0
    if results_path.exists():
        results = json.loads(results_path.read_text(encoding="utf-8"))
        for track in results.get("tracks", []):
            if (
                track.get("approved") is True
                and track.get("master_folder") == master_folder
                and track.get("main_genre") == main_genre
                and track.get("subgenre") == subgenre
            ):
                track["target_playlist_id"] = playlist_id
                updated_tracks += 1
        results_path.write_text(
            json.dumps(results, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return {
        "playlist_id": playlist_id,
        "playlist_url": playlist.get("external_urls", {}).get(
            "spotify", f"https://open.spotify.com/playlist/{playlist_id}"
        ),
        "created": created,
        "schema_destination": destination["name"],
        "updated_result_tracks": updated_tracks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="Exact Spotify playlist name")
    parser.add_argument("--master-folder", required=True)
    parser.add_argument("--main-genre", required=True)
    parser.add_argument("--subgenre", help="Omit to provision the main-genre fallback")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--description", default="DJ taxonomy destination playlist.")
    parser.add_argument("--private", action="store_true")
    args = parser.parse_args()

    result = provision(
        playlist_name=args.name,
        master_folder=args.master_folder,
        main_genre=args.main_genre,
        subgenre=args.subgenre,
        results_path=args.results,
        description=args.description,
        public=not args.private,
    )
    action = "Created" if result["created"] else "Reused"
    print(f"{action} Spotify playlist: {args.name}")
    print(f"Playlist ID: {result['playlist_id']}")
    print(f"Playlist URL: {result['playlist_url']}")
    print(f"Schema destination: {result['schema_destination']}")
    print(f"Updated approved tracks: {result['updated_result_tracks']}")


if __name__ == "__main__":
    main()
