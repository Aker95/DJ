"""Read-only Discogs and Last.fm evidence collection."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import pylast
import requests

from .config import Config


def collect_evidence(track: dict, config: Config) -> dict:
    """Collect independent evidence without scoring or choosing a genre."""

    return {
        "discogs": discogs_evidence(track, config.discogs_token),
        "lastfm": lastfm_evidence(
            track, config.lastfm_api_key, config.lastfm_shared_secret
        ),
    }


def discogs_evidence(track: dict, token: str | None) -> dict:
    if not token:
        return {"styles": [], "genres": [], "url": None, "warning": "DISCOGS_TOKEN is missing"}
    headers = {
        "User-Agent": "DJMusicResearch/1.0",
        "Authorization": f"Discogs token={token}",
    }
    queries = [
        {"type": "release", "artist": track.get("artist"), "track": track.get("title"), "per_page": 5},
        {"type": "release", "q": f"{track.get('artist', '')} {track.get('title', '')}", "per_page": 5},
    ]
    try:
        results: list[dict] = []
        for params in queries:
            response = requests.get(
                "https://api.discogs.com/database/search",
                params=params,
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
            results = response.json().get("results", [])
            if results:
                break
        if not results:
            return {"styles": [], "genres": [], "url": None, "warning": "No Discogs match"}
        result = results[0]
        resource = result.get("resource_url")
        url = resource.replace("api.discogs.com", "www.discogs.com") if resource else None
        return {
            "styles": result.get("style", []),
            "genres": result.get("genre", []),
            "label": (result.get("label") or [None])[0],
            "year": result.get("year"),
            "url": url,
            "warning": None,
        }
    except Exception as error:
        return {"styles": [], "genres": [], "url": None, "warning": str(error)}


def lastfm_evidence(track: dict, api_key: str | None, api_secret: str | None) -> dict:
    if not api_key or not api_secret:
        return {"tags": [], "source": None, "url": None, "warning": "Last.fm credentials are missing"}
    artist, title = track.get("artist", ""), track.get("title", "")
    try:
        network = pylast.LastFMNetwork(api_key=api_key, api_secret=api_secret)
        tags = [tag.item.get_name() for tag in network.get_track(artist, title).get_top_tags(limit=10)]
        source = "track"
        if not tags:
            tags = [tag.item.get_name() for tag in network.get_artist(artist).get_top_tags(limit=10)]
            source = "artist"
        url = f"https://www.last.fm/music/{quote(artist)}/_/{quote(title)}"
        return {
            "tags": tags,
            "source": source,
            "url": url,
            "warning": None if tags else "No Last.fm tags",
        }
    except Exception as error:
        return {"tags": [], "source": None, "url": None, "warning": str(error)}


def evidence_urls(track: dict) -> list[str]:
    urls = [track.get("spotify_url")]
    for provider in (track.get("provider_evidence") or {}).values():
        if isinstance(provider, dict):
            urls.append(provider.get("url"))
    urls.extend(
        source.get("url") if isinstance(source, dict) else source
        for source in track.get("web_evidence", [])
    )
    return [url for url in urls if url]
