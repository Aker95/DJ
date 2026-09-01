"""Research exact recordings and approve evidence-backed classifications."""

from __future__ import annotations

import argparse
import copy
from collections import Counter
from pathlib import Path
from urllib.parse import quote

import pylast
import requests
import spotify as spotify_library
from artifacts import now_iso, read_artifact, write_artifact, write_text
from catalog import (
    list_destinations,
    load_catalog,
    resolve_destination,
    validate_catalog,
)
from config import Config, load_config


def _prepare_document(playlist: dict, tracks: list[dict], schema: dict) -> dict:
    destinations = [
        {
            key: destination.get(key)
            for key in (
                "placement_id",
                "master_folder",
                "main_genre",
                "subgenre",
                "classification_level",
                "bpm_min",
                "bpm_max",
                "bpm_max_inclusive",
                "local_folder",
                "spotify_playlist",
                "spotify_playlist_name",
            )
        }
        for destination in list_destinations(schema)
    ]
    document = {
        "playlist": {
            "spotify_id": playlist["id"],
            "name": playlist["name"],
            "available_tracks": len(tracks),
            "unavailable_tracks": max(
                0, (playlist.get("total_items") or len(tracks)) - len(tracks)
            ),
        },
        "prepared_at": now_iso(),
        "schema_version": schema["_meta"]["version"],
        "taxonomy": {
            "main_genres": [
                {"name": item["name"], "aliases": item.get("aliases", [])}
                for item in schema.get("main_genres", [])
            ],
            "destinations": destinations,
        },
        "tracks": [_research_track(track, playlist["id"]) for track in tracks],
        "proposed_schema_changes": [],
        "summary": {},
    }
    return _refresh_summary(document)


def _research_track(track: dict, playlist_id: str) -> dict:
    return {
        **track,
        "source_playlist_id": playlist_id,
        "current_playlist_membership": [playlist_id],
        "provider_evidence": track.get("provider_evidence", {}),
        "web_evidence": [],
        "metadata_bpm": None,
        "metadata_bpm_source": None,
        "master_folder": None,
        "main_genre": None,
        "subgenre": None,
        "classification_level": None,
        "rationale": "",
        "alternatives_considered": [],
        "review_flags": [],
        "research_status": "pending",
        "provisional_destination": None,
        "proposes_new_taxonomy": False,
    }


def apply_decisions(document: dict, decisions: dict | None) -> dict:
    reviewed = copy.deepcopy(document)
    if not decisions:
        return _refresh_summary(reviewed)
    by_id = {track["spotify_track_id"]: track for track in reviewed.get("tracks", [])}
    overrides = decisions.get("tracks", [])
    if isinstance(overrides, dict):
        overrides = [
            {"spotify_track_id": spotify_id, **values}
            for spotify_id, values in overrides.items()
        ]
    for override in overrides:
        spotify_id = override.get("spotify_track_id")
        if spotify_id not in by_id:
            raise ValueError(
                f"Decision references unknown Spotify track {spotify_id!r}"
            )
        by_id[spotify_id].update(
            {
                key: copy.deepcopy(value)
                for key, value in override.items()
                if key != "spotify_track_id"
            }
        )
    if "schema_changes" in decisions:
        reviewed["accepted_schema_changes"] = copy.deepcopy(decisions["schema_changes"])
    return _refresh_summary(reviewed)


def _refresh_summary(document: dict) -> dict:
    tracks = document.get("tracks", [])
    main = Counter(
        track.get("main_genre") for track in tracks if track.get("main_genre")
    )
    sub = Counter(track.get("subgenre") for track in tracks if track.get("subgenre"))
    masters = Counter(
        track.get("master_folder") for track in tracks if track.get("master_folder")
    )
    document["summary"] = {
        "tracks": len(tracks),
        "available_tracks": document.get("playlist", {}).get(
            "available_tracks", len(tracks)
        ),
        "unavailable_tracks": document.get("playlist", {}).get("unavailable_tracks", 0),
        "pending": sum(not track.get("main_genre") for track in tracks),
        "parent_fallbacks": sum(
            bool(track.get("main_genre") and not track.get("subgenre"))
            for track in tracks
        ),
        "review_flagged": sum(bool(track.get("review_flags")) for track in tracks),
        "by_master_folder": dict(sorted(masters.items())),
        "by_main_genre": dict(sorted(main.items())),
        "by_subgenre": dict(sorted(sub.items())),
        "proposed_schema_changes": len(document.get("proposed_schema_changes", [])),
    }
    return document


def approve_research(
    document: dict, decisions: dict | None, schema: dict
) -> tuple[dict, dict]:
    reviewed = apply_decisions(document, decisions)
    validate_catalog(schema)
    errors: list[str] = []
    approved: list[dict] = []
    accepted_changes = reviewed.get("accepted_schema_changes", [])
    unaccepted_changes = [
        change
        for change in reviewed.get("proposed_schema_changes", [])
        if change not in accepted_changes
    ]
    if unaccepted_changes:
        errors.append(
            f"{len(unaccepted_changes)} proposed schema change(s) have not been explicitly accepted"
        )
    for track in reviewed.get("tracks", []):
        label = f"{track.get('artist', '')} — {track.get('title', '')}"
        if not track.get("main_genre") or not track.get("master_folder"):
            errors.append(f"{label}: main genre and BPM master are required")
            continue
        if not str(track.get("rationale", "")).strip():
            errors.append(f"{label}: research rationale is required")
        if track.get("research_status") in (None, "", "pending"):
            errors.append(f"{label}: exact-recording research is still pending")
        if not _evidence_urls(track):
            errors.append(f"{label}: at least one direct evidence URL is required")
        if track.get("proposes_new_taxonomy") and not track.get(
            "alternatives_considered"
        ):
            errors.append(
                f"{label}: explain which existing genres were considered before proposing taxonomy"
            )
        destination = resolve_destination(
            schema,
            track["master_folder"],
            track["main_genre"],
            track.get("subgenre"),
        )
        if destination is None:
            errors.append(f"{label}: selected destination is not present in the schema")
            continue
        track.update(
            {
                "research_status": "approved",
                "classification_level": destination["classification_level"],
                "provisional_destination": destination["placement_id"],
            }
        )
        approved.append(
            {
                **track,
                "approved": True,
                "placement_id": destination["placement_id"],
                "local_folder": destination["local_folder"],
                "target_playlist_id": destination.get("spotify_playlist"),
                "target_playlist_name": destination["spotify_playlist_name"],
            }
        )
    if errors:
        raise ValueError("Classification approval failed:\n- " + "\n- ".join(errors))
    classifications = {
        "source_playlist_id": reviewed["playlist"]["spotify_id"],
        "source_playlist_name": reviewed["playlist"]["name"],
        "approved_at": now_iso(),
        "tracks": approved,
        "summary": {"approved": len(approved)},
    }
    return classifications, _refresh_summary(reviewed)


def render_research(document: dict) -> str:
    playlist = document.get("playlist", {})
    summary = document.get("summary", {})
    lines = [
        f"# Playlist Research: {playlist.get('name', 'Unknown playlist')}",
        "",
        f"- Spotify playlist: `{playlist.get('spotify_id', '')}`",
        f"- Prepared: {document.get('prepared_at', 'unknown')}",
        f"- Schema version: {document.get('schema_version', 'unknown')}",
        f"- Tracks: {summary.get('tracks', 0)}",
        f"- Available tracks: {summary.get('available_tracks', summary.get('tracks', 0))}",
        f"- Unavailable tracks: {summary.get('unavailable_tracks', 0)}",
        f"- Pending: {summary.get('pending', 0)}",
        f"- Parent fallbacks: {summary.get('parent_fallbacks', 0)}",
        f"- Review flagged: {summary.get('review_flagged', 0)}",
        "",
        "## Breakdown",
        "",
    ]
    lines.extend(_count_lines("BPM masters", summary.get("by_master_folder", {})))
    lines.extend(_count_lines("Main genres", summary.get("by_main_genre", {})))
    lines.extend(_count_lines("Subgenres", summary.get("by_subgenre", {})))
    fallbacks = [
        track
        for track in document.get("tracks", [])
        if track.get("main_genre") and not track.get("subgenre")
    ]
    flagged = [
        track for track in document.get("tracks", []) if track.get("review_flags")
    ]
    lines.extend(["## Parent-genre fallbacks", ""])
    lines.extend(
        [
            f"- {track.get('artist', '')} — {track.get('title', '')}"
            for track in fallbacks
        ]
        or ["None."]
    )
    lines.extend(["", "## Tracks requiring special review", ""])
    lines.extend(
        [
            f"- {track.get('artist', '')} — {track.get('title', '')}: "
            f"{', '.join(track.get('review_flags', []))}"
            for track in flagged
        ]
        or ["None."]
    )
    lines.append("")
    lines.extend(["## Tracks", ""])
    for index, track in enumerate(document.get("tracks", []), 1):
        route = " / ".join(
            value
            for value in (
                track.get("master_folder") or "pending",
                track.get("main_genre") or "pending",
                track.get("subgenre") or "(parent)",
            )
        )
        providers = track.get("provider_evidence") or {}
        discogs = providers.get("discogs") or {}
        lastfm = providers.get("lastfm") or {}
        discogs_terms = [*discogs.get("styles", []), *discogs.get("genres", [])]
        lastfm_terms = lastfm.get("tags", [])
        lines.extend(
            [
                f"### {index}. {track.get('artist', '')} — {track.get('title', '')}",
                "",
                f"- Route: {route}",
                f"- Version: {track.get('album') or 'unknown'} ({track.get('release_date') or 'unknown'})",
                f"- ISRC: `{track.get('isrc') or 'unknown'}`",
                f"- Duration: {track.get('duration_ms') or 'unknown'} ms",
                f"- BPM evidence: {track.get('metadata_bpm') or 'none'} ({track.get('metadata_bpm_source') or 'none'})",
                f"- Discogs evidence: {', '.join(discogs_terms) or discogs.get('warning') or 'none'}",
                f"- Last.fm evidence: {', '.join(lastfm_terms) or lastfm.get('warning') or 'none'} ({lastfm.get('source') or 'no source level'})",
                f"- Rationale: {track.get('rationale') or 'Pending research'}",
                f"- Alternatives considered: {', '.join(track.get('alternatives_considered') or []) or 'none recorded'}",
                f"- Flags: {', '.join(track.get('review_flags') or []) or 'none'}",
                "- Sources:",
            ]
        )
        urls = _evidence_urls(track)
        lines.extend([f"  - {url}" for url in urls] or ["  - Pending research"])
        lines.append("")
    changes = document.get("proposed_schema_changes", [])
    lines.extend(["## Proposed schema changes", ""])
    if changes:
        lines.extend(f"- `{item.get('action')}`: {item}" for item in changes)
    else:
        lines.append("None.")
    return "\n".join(lines).rstrip() + "\n"


def _count_lines(label: str, counts: dict) -> list[str]:
    lines = [f"### {label}", ""]
    lines.extend(
        [f"- {name}: {count}" for name, count in counts.items()] or ["- None yet"]
    )
    lines.append("")
    return lines


def _collect_evidence(track: dict, config: Config) -> dict:
    return {
        "discogs": _discogs_evidence(track, config.discogs_token),
        "lastfm": _lastfm_evidence(
            track, config.lastfm_api_key, config.lastfm_shared_secret
        ),
    }


def _discogs_evidence(track: dict, token: str | None) -> dict:
    if not token:
        return {
            "styles": [],
            "genres": [],
            "url": None,
            "warning": "DISCOGS_TOKEN is missing",
        }
    headers = {
        "User-Agent": "DJMusicResearch/1.0",
        "Authorization": f"Discogs token={token}",
    }
    queries = [
        {
            "type": "release",
            "artist": track.get("artist"),
            "track": track.get("title"),
            "per_page": 5,
        },
        {
            "type": "release",
            "q": f"{track.get('artist', '')} {track.get('title', '')}",
            "per_page": 5,
        },
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
            return {
                "styles": [],
                "genres": [],
                "url": None,
                "warning": "No Discogs match",
            }
        result = results[0]
        resource = result.get("resource_url")
        return {
            "styles": result.get("style", []),
            "genres": result.get("genre", []),
            "label": (result.get("label") or [None])[0],
            "year": result.get("year"),
            "url": resource.replace("api.discogs.com", "www.discogs.com")
            if resource
            else None,
            "warning": None,
        }
    except Exception as error:  # noqa: BLE001 - provider failures are recorded as evidence warnings
        return {"styles": [], "genres": [], "url": None, "warning": str(error)}


def _lastfm_evidence(track: dict, api_key: str | None, api_secret: str | None) -> dict:
    if not api_key or not api_secret:
        return {
            "tags": [],
            "source": None,
            "url": None,
            "warning": "Last.fm credentials are missing",
        }
    artist, title = track.get("artist", ""), track.get("title", "")
    try:
        network = pylast.LastFMNetwork(api_key=api_key, api_secret=api_secret)
        tags = [
            tag.item.get_name()
            for tag in network.get_track(artist, title).get_top_tags(limit=10)
        ]
        source = "track"
        if not tags:
            tags = [
                tag.item.get_name()
                for tag in network.get_artist(artist).get_top_tags(limit=10)
            ]
            source = "artist"
        return {
            "tags": tags,
            "source": source,
            "url": f"https://www.last.fm/music/{quote(artist)}/_/{quote(title)}",
            "warning": None if tags else "No Last.fm tags",
        }
    except Exception as error:  # noqa: BLE001 - provider failures are recorded as evidence warnings
        return {"tags": [], "source": None, "url": None, "warning": str(error)}


def _evidence_urls(track: dict) -> list[str]:
    urls = [track.get("spotify_url")]
    for provider in (track.get("provider_evidence") or {}).values():
        if isinstance(provider, dict):
            urls.append(provider.get("url"))
    urls.extend(
        source.get("url") if isinstance(source, dict) else source
        for source in track.get("web_evidence", [])
    )
    return [url for url in urls if url]


def research_playlist(playlist_value: str, config: Config | None = None) -> dict:
    config = config or load_config()
    catalog = load_catalog(config.schema_path)
    spotify = spotify_library.authenticate(config)
    playlist, tracks = spotify_library.read_playlist(spotify, playlist_value, rich=True)
    for index, track in enumerate(tracks, 1):
        print(
            f"Collecting evidence {index}/{len(tracks)}: {track['artist']} — {track['title']}"
        )
        track["provider_evidence"] = _collect_evidence(track, config)
    document = _prepare_document(playlist, tracks, catalog)
    write_artifact(config.data_dir / "research.json", "research", document)
    write_text(config.data_dir / "research.md", render_research(document))
    return document


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    research = commands.add_parser("research")
    research.add_argument("--playlist", required=True)
    render = commands.add_parser("render")
    render.add_argument(
        "research", type=Path, nargs="?", default=Path("data/research.json")
    )
    approve = commands.add_parser("approve")
    approve.add_argument(
        "research", type=Path, nargs="?", default=Path("data/research.json")
    )
    approve.add_argument("--decisions", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    config = load_config()
    if args.command == "research":
        document = research_playlist(args.playlist, config)
        print(f"Prepared {document['summary']['tracks']} track(s).")
        return
    document = read_artifact(args.research, "research")
    if args.command == "render":
        document = _refresh_summary(document)
        write_artifact(args.research, "research", document)
        write_text(config.data_dir / "research.md", render_research(document))
        print("Regenerated data/research.md.")
        return
    decisions = read_artifact(args.decisions) if args.decisions else None
    classifications, reviewed = approve_research(
        document, decisions, load_catalog(config.schema_path)
    )
    write_artifact(
        config.data_dir / "classifications.json", "classifications", classifications
    )
    write_artifact(config.data_dir / "research.json", "research", reviewed)
    write_text(config.data_dir / "research.md", render_research(reviewed))
    print(f"Approved {classifications['summary']['approved']} track(s).")


if __name__ == "__main__":
    main()
