"""Spotify authentication, playlist reading, snapshots, provisioning, and sorting."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import spotipy
from artifacts import (
    fingerprint_artifact,
    fingerprint_data,
    now_iso,
    read_artifact,
    write_artifact,
)
from catalog import (
    canonical_key,
    list_destinations,
    load_catalog,
    resolve_destination,
    save_catalog,
    set_spotify_playlist,
)
from config import Config, load_config
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

READ_SCOPE = "playlist-read-private playlist-read-collaborative"
WRITE_SCOPE = f"{READ_SCOPE} playlist-modify-private playlist-modify-public"


def _get_client(
    config: Config, scope: str = READ_SCOPE, open_browser: bool = False
) -> spotipy.Spotify:
    config.require_spotify()
    config.spotify_cache.parent.mkdir(parents=True, exist_ok=True)
    auth = SpotifyOAuth(
        client_id=config.spotify_client_id,
        client_secret=config.spotify_client_secret,
        redirect_uri=config.spotify_redirect_uri,
        scope=scope,
        cache_path=str(config.spotify_cache),
        open_browser=open_browser,
    )
    return spotipy.Spotify(auth_manager=auth)


def _fetch_playlists(spotify: Any) -> list[dict]:
    playlists: list[dict] = []
    page = spotify.current_user_playlists(limit=50)
    while page:
        playlists.extend(page.get("items", []))
        page = spotify.next(page) if page.get("next") else None
    return playlists


def _fetch_playlist_tracks(
    spotify: Any, playlist_id: str, rich: bool = False
) -> list[dict] | None:
    fields = (
        "items(item(id,name,artists(id,name),album(id,name,release_date),duration_ms,"
        "external_ids(isrc),external_urls(spotify))),next"
        if rich
        else "items(item(id,name,artists(id,name),duration_ms)),next"
    )
    try:
        page = spotify.playlist_items(
            playlist_id, fields=fields, additional_types=["track"], limit=100
        )
    except SpotifyException as error:
        if error.http_status == 403:
            return None
        raise
    tracks: list[dict] = []
    while page:
        for item in page.get("items", []):
            track = item.get("item") or item.get("track")
            if not track or not track.get("id"):
                continue
            artists = track.get("artists", [])
            record = {
                "spotify_track_id": track["id"],
                "artist": ", ".join(artist.get("name", "") for artist in artists),
                "artist_ids": [
                    artist.get("id") for artist in artists if artist.get("id")
                ],
                "title": track.get("name", ""),
                "duration_ms": track.get("duration_ms"),
            }
            if rich:
                album = track.get("album") or {}
                record.update(
                    {
                        "album": album.get("name"),
                        "spotify_album_id": album.get("id"),
                        "release_date": album.get("release_date"),
                        "isrc": (track.get("external_ids") or {}).get("isrc"),
                        "spotify_url": (track.get("external_urls") or {}).get(
                            "spotify"
                        ),
                    }
                )
            tracks.append(record)
        page = spotify.next(page) if page.get("next") else None
    return tracks


def _resolve_playlist(spotify: Any, value: str) -> dict:
    candidate = value.strip()
    if candidate.startswith("spotify:playlist:"):
        candidate = candidate.rsplit(":", 1)[-1]
    elif "open.spotify.com" in candidate:
        parts = [part for part in urlparse(candidate).path.split("/") if part]
        if len(parts) >= 2 and parts[-2] == "playlist":
            candidate = parts[-1]
    if len(candidate) == 22 and candidate.isalnum():
        metadata = spotify.playlist(candidate, fields="id,name,tracks.total")
        return {
            "id": metadata["id"],
            "name": metadata.get("name", candidate),
            "total_items": (metadata.get("tracks") or {}).get("total"),
        }
    normalized = " ".join(value.split()).casefold()
    matches = [
        playlist
        for playlist in _fetch_playlists(spotify)
        if " ".join((playlist.get("name") or "").split()).casefold() == normalized
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one playlist matching {value!r}; found {len(matches)}"
        )
    return {
        "id": matches[0]["id"],
        "name": matches[0].get("name", value),
        "total_items": (matches[0].get("tracks") or {}).get("total"),
    }


def _playlist_track_ids(spotify: Any, playlist_id: str) -> set[str]:
    return {
        track["spotify_track_id"]
        for track in (_fetch_playlist_tracks(spotify, playlist_id) or [])
    }


def _add_tracks(spotify: Any, playlist_id: str, track_ids: list[str]) -> None:
    for offset in range(0, len(track_ids), 100):
        batch = track_ids[offset : offset + 100]
        spotify.playlist_add_items(playlist_id, batch)


def _remove_tracks(spotify: Any, playlist_id: str, track_ids: list[str]) -> None:
    for offset in range(0, len(track_ids), 100):
        batch = track_ids[offset : offset + 100]
        spotify.playlist_remove_all_occurrences_of_items(playlist_id, batch)


def authenticate(config: Config, *, write: bool = False, open_browser: bool = False):
    return _get_client(
        config, WRITE_SCOPE if write else READ_SCOPE, open_browser=open_browser
    )


def read_playlist(
    spotify: Any, value: str, rich: bool = True
) -> tuple[dict, list[dict]]:
    playlist = _resolve_playlist(spotify, value)
    tracks = _fetch_playlist_tracks(spotify, playlist["id"], rich=rich)
    if tracks is None:
        raise ValueError("The Spotify playlist is not accessible")
    return playlist, tracks


def _destination_indexes(
    catalog: dict,
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    by_id: dict[str, list[dict]] = defaultdict(list)
    by_name: dict[str, list[dict]] = defaultdict(list)
    for destination in list_destinations(catalog):
        if destination.get("spotify_playlist"):
            by_id[destination["spotify_playlist"]].append(destination)
        by_name[canonical_key(destination["spotify_playlist_name"])].append(destination)
    return by_id, by_name


def _match_destination(playlist: dict, indexes: tuple[dict, dict]) -> dict | None:
    by_id, by_name = indexes
    matches = by_id.get(playlist["id"], [])
    if len(matches) == 1:
        return matches[0]
    named = by_name.get(canonical_key(playlist.get("name", "")), [])
    return named[0] if len(named) == 1 else None


def build_snapshot(spotify: Any, catalog: dict) -> dict:
    playlists: list[dict] = []
    inaccessible: list[str] = []
    skipped_bags: list[str] = []
    indexes = _destination_indexes(catalog)
    for playlist in _fetch_playlists(spotify):
        name = (playlist.get("name") or "").strip()
        if set(canonical_key(name).split()) & {"bag", "staging"}:
            skipped_bags.append(name)
            continue
        destination = _match_destination(playlist, indexes)
        if destination is None:
            continue
        tracks = _fetch_playlist_tracks(spotify, playlist["id"], rich=True)
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
        "created_at": now_iso(),
        "playlists": playlists,
        "summary": {
            "playlists": len(playlists),
            "tracks": sum(len(item["tracks"]) for item in playlists),
            "skipped_bags": skipped_bags,
            "inaccessible": inaccessible,
        },
    }


def build_provision_plan(spotify: Any, destination: dict, public: bool = False) -> dict:
    playlists = _fetch_playlists(spotify)
    mapped_id = destination.get("spotify_playlist")
    mapped = next((item for item in playlists if item.get("id") == mapped_id), None)
    if mapped_id and not mapped:
        raise ValueError(f"Mapped Spotify playlist {mapped_id!r} is not accessible")
    matches = [
        item
        for item in playlists
        if canonical_key(item.get("name", ""))
        == canonical_key(destination["spotify_playlist_name"])
    ]
    if len(matches) > 1:
        raise ValueError(
            f"Multiple playlists are named {destination['spotify_playlist_name']!r}"
        )
    selected = mapped or (matches[0] if matches else None)
    return {
        "created_at": now_iso(),
        "placement_id": destination["placement_id"],
        "canonical_name": destination["spotify_playlist_name"],
        "current_playlist_id": mapped_id,
        "selected_playlist_id": selected.get("id") if selected else None,
        "action": "already_mapped" if mapped else "reuse" if selected else "create",
        "public": public,
    }


def apply_provision_plan(spotify: Any, catalog: dict, plan: dict) -> tuple[dict, dict]:
    if plan["action"] == "create":
        current_matches = [
            item
            for item in _fetch_playlists(spotify)
            if canonical_key(item.get("name", ""))
            == canonical_key(plan["canonical_name"])
        ]
        if len(current_matches) > 1:
            raise ValueError(f"Multiple playlists are named {plan['canonical_name']!r}")
        if current_matches:
            playlist_id = current_matches[0]["id"]
        else:
            user_id = spotify.current_user()["id"]
            playlist_id = spotify.user_playlist_create(
                user_id,
                plan["canonical_name"],
                public=bool(plan.get("public")),
                description="DJ library canonical destination.",
            )["id"]
    else:
        playlist_id = plan["selected_playlist_id"]
        if playlist_id not in {item.get("id") for item in _fetch_playlists(spotify)}:
            raise ValueError("The planned Spotify playlist is no longer accessible")
    updated = set_spotify_playlist(catalog, plan["placement_id"], playlist_id)
    return updated, {**plan, "playlist_id": playlist_id, "applied_at": now_iso()}


def build_sort_plan(classifications: dict, remove_source: bool = True) -> dict:
    actions, skipped = [], []
    for track in classifications.get("tracks", []):
        if not track.get("target_playlist_id"):
            skipped.append(
                {
                    "spotify_track_id": track.get("spotify_track_id"),
                    "reason": "playlist-not-provisioned",
                }
            )
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
        "actions": actions,
        "skipped": skipped,
        "summary": {"tracks": len(actions), "skipped": len(skipped)},
    }


def apply_sort_plan(spotify: Any, plan: dict) -> dict:
    by_target: dict[str, list[str]] = defaultdict(list)
    for track in plan.get("actions", []):
        by_target[track["target_playlist_id"]].append(track["spotify_track_id"])
    added = 0
    for playlist_id, track_ids in by_target.items():
        existing = _playlist_track_ids(spotify, playlist_id)
        missing = [track_id for track_id in track_ids if track_id not in existing]
        _add_tracks(spotify, playlist_id, missing)
        if set(track_ids) - _playlist_track_ids(spotify, playlist_id):
            raise RuntimeError(
                f"Spotify addition verification failed for {playlist_id}"
            )
        added += len(missing)
    if plan.get("remove_source"):
        removable = [
            item["spotify_track_id"]
            for item in plan.get("actions", [])
            if item["target_playlist_id"] != plan["source_playlist_id"]
        ]
        present = _playlist_track_ids(spotify, plan["source_playlist_id"])
        _remove_tracks(
            spotify,
            plan["source_playlist_id"],
            [item for item in removable if item in present],
        )
        remaining = set(removable) & _playlist_track_ids(
            spotify, plan["source_playlist_id"]
        )
        if remaining:
            raise RuntimeError(f"Tracks remain in source playlist: {sorted(remaining)}")
    return {
        "applied_at": now_iso(),
        "added": added,
        "satisfied": len(plan.get("actions", [])),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("auth")
    commands.add_parser("snapshot")
    provision = commands.add_parser("provision")
    provision.add_argument("--master")
    provision.add_argument("--main")
    provision.add_argument("--subgenre")
    provision.add_argument("--public", action="store_true")
    provision.add_argument("--apply", action="store_true")
    provision.add_argument(
        "--plan", type=Path, default=Path("data/spotify_provision_plan.json")
    )
    sort = commands.add_parser("sort")
    sort.add_argument(
        "classifications",
        type=Path,
        nargs="?",
        default=Path("data/classifications.json"),
    )
    sort.add_argument("--keep-source", action="store_true")
    sort.add_argument("--apply", action="store_true")
    sort.add_argument("--plan", type=Path, default=Path("data/spotify_sort_plan.json"))
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    config = load_config()
    if args.command == "auth":
        authenticate(config, write=True, open_browser=True).current_user()
        print("Spotify authentication is ready.")
        return
    catalog = load_catalog(config.schema_path)
    if args.command == "snapshot":
        snapshot = build_snapshot(authenticate(config), catalog)
        write_artifact(
            config.data_dir / "spotify_snapshot.json", "spotify-snapshot", snapshot
        )
        print(f"Snapshot saved: {snapshot['summary']['tracks']} track(s).")
        return
    spotify_client = authenticate(config, write=True)
    if args.command == "provision":
        if args.apply:
            plan = read_artifact(args.plan, "spotify-provision-plan")
            if (plan.get("inputs") or {}).get("catalog") != fingerprint_data(catalog):
                raise ValueError("Spotify provision plan is stale; preview it again")
            updated, result = apply_provision_plan(spotify_client, catalog, plan)
            save_catalog(updated, config.schema_path)
            write_artifact(
                config.data_dir / "spotify_provision_result.json",
                "spotify-provision-result",
                result,
            )
            print(f"Mapped {result['canonical_name']} to {result['playlist_id']}.")
            return
        if not args.master or not args.main:
            raise ValueError("Provision preview requires --master and --main")
        destination = resolve_destination(
            catalog, args.master, args.main, args.subgenre
        )
        if destination is None:
            raise ValueError("The requested catalog destination does not exist")
        plan = build_provision_plan(spotify_client, destination, args.public)
        plan["inputs"] = {"catalog": fingerprint_data(catalog)}
        write_artifact(args.plan, "spotify-provision-plan", plan)
        print(f"Plan: {plan['action'].replace('_', ' ')} {plan['canonical_name']}.")
        return
    if args.apply:
        plan = read_artifact(args.plan, "spotify-sort-plan")
        source_path = Path((plan.get("input_paths") or {}).get("classifications", ""))
        expected = (plan.get("inputs") or {}).get("classifications")
        if not source_path.is_file() or fingerprint_artifact(source_path) != expected:
            raise ValueError("Spotify sort plan is stale; preview it again")
        result = apply_sort_plan(spotify_client, plan)
        write_artifact(
            config.data_dir / "spotify_sort_result.json", "spotify-sort-result", result
        )
        print(f"Spotify sort verified for {result['satisfied']} track(s).")
        return
    classifications = read_artifact(args.classifications, "classifications")
    plan = build_sort_plan(classifications, not args.keep_source)
    plan["inputs"] = {"classifications": fingerprint_artifact(args.classifications)}
    plan["input_paths"] = {"classifications": str(args.classifications.resolve())}
    write_artifact(args.plan, "spotify-sort-plan", plan)
    print(
        f"Planned {plan['summary']['tracks']} track(s); skipped {plan['summary']['skipped']}."
    )


if __name__ == "__main__":
    main()
