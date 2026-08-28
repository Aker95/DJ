"""Spotify authentication, pagination, and small playlist operations."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from .config import Config

READ_SCOPE = "playlist-read-private playlist-read-collaborative"
WRITE_SCOPE = f"{READ_SCOPE} playlist-modify-private playlist-modify-public"


def get_client(config: Config, scope: str = READ_SCOPE, open_browser: bool = False) -> spotipy.Spotify:
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


def fetch_playlists(spotify: Any) -> list[dict]:
    playlists: list[dict] = []
    page = spotify.current_user_playlists(limit=50)
    while page:
        playlists.extend(page.get("items", []))
        page = spotify.next(page) if page.get("next") else None
    return playlists


def fetch_playlist_tracks(spotify: Any, playlist_id: str, rich: bool = False) -> list[dict] | None:
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
                "artist_ids": [artist.get("id") for artist in artists if artist.get("id")],
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
                        "spotify_url": (track.get("external_urls") or {}).get("spotify"),
                    }
                )
            tracks.append(record)
        page = spotify.next(page) if page.get("next") else None
    return tracks


def resolve_playlist(spotify: Any, value: str) -> dict:
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
        for playlist in fetch_playlists(spotify)
        if " ".join((playlist.get("name") or "").split()).casefold() == normalized
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one playlist matching {value!r}; found {len(matches)}")
    return {
        "id": matches[0]["id"],
        "name": matches[0].get("name", value),
        "total_items": (matches[0].get("tracks") or {}).get("total"),
    }


def playlist_track_ids(spotify: Any, playlist_id: str) -> set[str]:
    return {
        track["spotify_track_id"]
        for track in (fetch_playlist_tracks(spotify, playlist_id) or [])
    }


def add_tracks(spotify: Any, playlist_id: str, track_ids: list[str]) -> None:
    for offset in range(0, len(track_ids), 100):
        batch = track_ids[offset : offset + 100]
        spotify.playlist_add_items(playlist_id, batch)


def remove_tracks(spotify: Any, playlist_id: str, track_ids: list[str]) -> None:
    for offset in range(0, len(track_ids), 100):
        batch = track_ids[offset : offset + 100]
        spotify.playlist_remove_all_occurrences_of_items(playlist_id, batch)
