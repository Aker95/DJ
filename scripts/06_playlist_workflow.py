#!/usr/bin/env python3
"""
06_playlist_workflow.py — Human-researched Spotify playlist classifier and sorter.

PART 1 — PREPARE RESEARCH
  Fetches rich track metadata from a Spotify playlist and writes provisional
  JSON + Markdown artifacts.  The artifacts are intentionally unclassified:
  an agent/reviewer researches each exact recording on the public web and adds
  a recommendation, reasoning, confidence, and direct source URLs.

  Nothing on Spotify is touched during this step.

PART 1B — VALIDATE / APPROVE RESEARCH
  Validates the researched JSON, applies explicitly proposed schema additions,
  and converts the reviewed recommendations to classify_results.json.
  This step also makes no Spotify API calls.

PART 2 — SORT
  Reads classify_results.json, routes approved classifications, then for
  each confident track:
    • adds the track to the correct subgenre playlist on Spotify
    • removes the track from the source playlist

Usage (CLI):
  python scripts/06_playlist_workflow.py --prepare-research --playlist "unsorted"
  python scripts/06_playlist_workflow.py --validate-research --input classification_research_unsorted.json
  python scripts/06_playlist_workflow.py --approve-research --input classification_research_unsorted.json
  python scripts/06_playlist_workflow.py --sort [--input classify_results.json] [--min-confidence 0.5] [--dry-run] [--no-remove]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from difflib import get_close_matches
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))

from utils.config import load_env, load_schema, save_schema
from utils.schema import (
    canonical_key,
    destination_index,
    is_v2,
    iter_destinations,
    iter_genre_placements,
    iter_subgenres,
    main_genre_index,
    resolve_destination,
    slug as taxonomy_slug,
    subgenre_index,
    validate_schema,
)
from utils.spotify_auth import get_spotify_client

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parents[1]
DEFAULT_RESULTS_PATH = _PROJECT_ROOT / "classify_results.json"
DEFAULT_SCHEMA_PATH = _PROJECT_ROOT / "music_schema_knowledge.json"

# Spotify API batch limit for add/remove calls
_SPOTIFY_BATCH_SIZE = 100

# Scope required to write to playlists
_WRITE_SCOPE = "playlist-read-private playlist-modify-private playlist-modify-public"

_RESEARCH_STATUSES = {
    "pending", "proposed", "approved", "rejected", "uncertain", "recommended"
}
_REVIEW_FLAGS = {
    "artist-only",
    "broad-source",
    "conflicting-sources",
    "inferred-from-metadata",
    "new-taxonomy",
    "low-evidence",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_playlist_id(sp: Any, id_or_name: str) -> str:
    """Return a Spotify playlist ID.

    Accepts a raw 22-character ID, ``spotify:playlist:...`` URI, Spotify URL,
    or an exact playlist name (case-insensitive).

    Raises ValueError if no matching playlist is found.
    """
    candidate = id_or_name.strip()

    if candidate.startswith("spotify:playlist:"):
        candidate = candidate.rsplit(":", 1)[-1]
    elif "open.spotify.com" in candidate:
        parsed = urlparse(candidate)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[-2] == "playlist":
            candidate = parts[-1]

    # Spotify IDs are exactly 22 base-62 characters.
    if len(candidate) == 22 and candidate.isalnum():
        return candidate

    name_lower = " ".join(id_or_name.split()).casefold()
    available_names: list[str] = []
    page = sp.current_user_playlists(limit=50)
    while page:
        for playlist in page["items"]:
            available_names.append(playlist["name"])
            normalized_playlist_name = " ".join(playlist["name"].split()).casefold()
            if normalized_playlist_name == name_lower:
                return playlist["id"]
        page = sp.next(page) if page.get("next") else None

    query_tokens = {token for token in name_lower.split() if len(token) > 2}
    related = [
        name
        for name in available_names
        if query_tokens.intersection(name.strip().casefold().split())
    ]
    suggestions = related[:8] or get_close_matches(
        id_or_name.strip(), available_names, n=5, cutoff=0.35
    )
    suggestion_text = (
        f" Similar account playlists: {', '.join(repr(name) for name in suggestions)}."
        if suggestions
        else ""
    )
    raise ValueError(
        f"No Spotify playlist found matching {id_or_name!r}. "
        "Pass an exact name or a 22-character Spotify playlist ID."
        + suggestion_text
    )


def _build_subgenre_index(schema: dict) -> dict[str, str | None]:
    """Return {subgenre_name: spotify_playlist_id} for every subgenre in the schema."""
    index: dict[str, str | None] = {}
    for folder in schema.get("master_folders", []):
        for subgenre in folder.get("subgenres", []):
            index[subgenre["name"]] = subgenre.get("spotify_playlist")
    return index


def _fetch_all_playlist_tracks(sp: Any, playlist_id: str) -> list[dict]:
    """Paginate through a playlist and return normalized rich track metadata."""
    tracks: list[dict] = []
    page = sp.playlist_items(
        playlist_id,
        fields=(
            "items(item(id,name,artists(id,name),album(id,name,release_date),"
            "duration_ms,external_ids(isrc),external_urls(spotify))),next"
        ),
        additional_types=["track"],
        limit=_SPOTIFY_BATCH_SIZE,
    )
    while page:
        for item in page.get("items", []):
            # ``item`` is the current Spotify response shape used by this
            # project; accept ``track`` as well for older Spotipy responses.
            track = item.get("item") or item.get("track")
            # Skip local files / unavailable / null entries
            if not track or not track.get("id"):
                continue
            artists = [
                {
                    "spotify_artist_id": artist.get("id"),
                    "name": artist.get("name", ""),
                }
                for artist in track.get("artists", [])
            ]
            album = track.get("album") or {}
            tracks.append(
                {
                    "spotify_track_id": track["id"],
                    "artists": artists,
                    "artist": ", ".join(
                        artist["name"] for artist in artists if artist["name"]
                    ),
                    "title": track.get("name", ""),
                    "album": album.get("name"),
                    "spotify_album_id": album.get("id"),
                    "isrc": (track.get("external_ids") or {}).get("isrc"),
                    "duration_ms": track.get("duration_ms"),
                    "release_date": album.get("release_date"),
                    "spotify_url": (track.get("external_urls") or {}).get("spotify"),
                }
            )
        page = sp.next(page) if page.get("next") else None
    return tracks


# ---------------------------------------------------------------------------
# PART 1 — prepare web research
# ---------------------------------------------------------------------------


def _slugify(value: str) -> str:
    """Return a filesystem-safe lowercase slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "playlist"


def _schema_indexes(schema: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """Return subgenre and master-folder indexes keyed by exact name."""
    subgenres: dict[str, dict] = {}
    master_folders: dict[str, dict] = {}
    for folder in schema.get("master_folders", []):
        master_folders[folder["name"]] = folder
        for subgenre in folder.get("subgenres", []):
            subgenres[subgenre["name"]] = {
                **subgenre,
                "master_folder": folder["name"],
            }
    return subgenres, master_folders


def _blank_research_track(track: dict) -> dict:
    """Attach blank v2 Codex-review fields to Spotify metadata."""
    return {
        **track,
        "main_genre": None,
        "subgenre": None,
        "classification_level": None,
        "master_folder": None,
        "confidence": 0.0,
        "rationale": "",
        "evidence": [],
        "review_flags": [],
        "review_status": "pending",
        "is_new_main_genre_proposal": False,
        "is_new_subgenre_proposal": False,
        "target_playlist_id": None,
        "target_kind": None,
        "already_in_target_playlist": False,
    }


def prepare_research(
    playlist_id_or_name: str,
    *,
    env: dict | None = None,
    schema: dict | None = None,
    json_output_path: str | Path | None = None,
    markdown_output_path: str | Path | None = None,
) -> dict:
    """Fetch rich Spotify metadata and create provisional research artifacts."""
    if env is None:
        env = load_env()
    if schema is None:
        schema = load_schema()

    sp = get_spotify_client(scope="playlist-read-private")
    playlist_id = _resolve_playlist_id(sp, playlist_id_or_name)

    # Fetch playlist metadata (name)
    playlist_meta = sp.playlist(playlist_id, fields="name")
    playlist_name: str = playlist_meta.get("name", playlist_id)

    raw_tracks = _fetch_all_playlist_tracks(sp, playlist_id)
    research_tracks = [_blank_research_track(track) for track in raw_tracks]
    output: dict = {
        "artifact_type": "spotify_genre_research",
        "artifact_version": 2,
        "status": "provisional",
        "source_playlist_id": playlist_id,
        "source_playlist_name": playlist_name,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": schema.get("_meta", {}).get("version"),
        "taxonomy_context": {
            "main_genres": [
                {
                    "name": genre["name"],
                    "aliases": genre.get("aliases", []),
                }
                for genre in schema.get("main_genres", [])
            ],
            "destinations": [
                {
                    "master_folder": destination["master_folder"],
                    "main_genre": destination.get("main_genre"),
                    "subgenre": destination.get("subgenre"),
                    "classification_level": destination["classification_level"],
                }
                for destination in iter_destinations(schema)
            ],
            "policy": [
                "Every valid track must receive at least a canonical main genre.",
                "Use a parent fallback plus review flags when a subtype is not defensible.",
                "Artist-profile evidence is allowed; broad/conflicting labels require documented judgment.",
                "Propose precise new taxonomy nodes, but merge synonyms as aliases.",
            ],
        },
        "total_tracks": len(research_tracks),
        "tracks": research_tracks,
        "classification_policy": "codex_web_research_v2",
        "proposed_main_genres": [],
        "proposed_subgenres": [],
        "proposed_aliases": [],
        "proposed_routing_changes": [],
        "summary": {
            "by_main_genre": {},
            "by_subgenre": {},
            "parent_fallbacks": 0,
            "pending_classification": len(research_tracks),
            "review_flagged": 0,
            "proposed_main_genres": 0,
            "proposed_subgenres": 0,
            "already_in_correct_playlist": 0,
        },
    }

    slug = _slugify(playlist_name)
    json_path = (
        Path(json_output_path)
        if json_output_path
        else _PROJECT_ROOT / f"classification_research_{slug}.json"
    )
    markdown_path = (
        Path(markdown_output_path)
        if markdown_output_path
        else _PROJECT_ROOT / f"classification_research_{slug}.md"
    )
    json_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_research_markdown(output), encoding="utf-8")
    print(f"\nProvisional research JSON written to {json_path}")
    print(f"Provisional research report written to {markdown_path}")

    return output


def classify_playlist(
    playlist_id_or_name: str,
    *,
    env: dict | None = None,
    schema: dict | None = None,
    output_path: str | Path | None = None,
) -> dict:
    """Backward-compatible alias for the metadata-only research preparation.

    Unlike the previous implementation, this performs no Discogs/Last.fm
    classification. ``output_path`` names the provisional JSON artifact.
    """
    return prepare_research(
        playlist_id_or_name,
        env=env,
        schema=schema,
        json_output_path=output_path,
    )


def _source_url(source: Any) -> str | None:
    if isinstance(source, str):
        return source
    if isinstance(source, dict):
        return source.get("url")
    return None


def _source_label(source: Any) -> str:
    if isinstance(source, str):
        return source
    if isinstance(source, dict):
        return source.get("title") or source.get("provider") or source.get("url", "source")
    return "source"


def _render_research_markdown_v2(research: dict) -> str:
    """Render the complete Codex/user review contract for artifact v2."""
    summary = research.get("summary", {})
    lines = [
        f"# Genre Research: {research.get('source_playlist_name', 'Spotify playlist')}",
        "",
        f"- Status: **{research.get('status', 'provisional')}**",
        f"- Spotify playlist ID: `{research.get('source_playlist_id', '')}`",
        f"- Tracks: {research.get('total_tracks', len(research.get('tracks', [])))}",
        f"- Pending classification: {summary.get('pending_classification', 0)}",
        f"- Parent-genre fallbacks: {summary.get('parent_fallbacks', 0)}",
        f"- Review-flagged: {summary.get('review_flagged', 0)}",
        f"- Proposed main genres: {summary.get('proposed_main_genres', 0)}",
        f"- Proposed subgenres: {summary.get('proposed_subgenres', 0)}",
        "",
        "## Classification summary",
        "",
        "| Main genre | Tracks |",
        "|---|---:|",
    ]
    by_main = summary.get("by_main_genre", {})
    if by_main:
        for genre, count in sorted(by_main.items()):
            lines.append(f"| {genre} | {count} |")
    else:
        lines.append("| _Pending Codex research_ | 0 |")

    lines.extend(["", "| Subgenre / parent fallback | Tracks |", "|---|---:|"])
    by_subgenre = summary.get("by_subgenre", {})
    if by_subgenre:
        for subgenre, count in sorted(by_subgenre.items()):
            lines.append(f"| {subgenre} | {count} |")
    else:
        lines.append("| _Pending Codex research_ | 0 |")

    lines.extend(
        [
            "",
            "## Track review",
            "",
            "| # | Track | Main genre | Subgenre | Master folder | Confidence | Flags | Status |",
            "|---:|---|---|---|---|---:|---|---|",
        ]
    )
    for index, track in enumerate(research.get("tracks", []), start=1):
        flags = ", ".join(track.get("review_flags", [])) or "—"
        lines.append(
            f"| {index} | {track.get('artist', '')} — {track.get('title', '')} | "
            f"{track.get('main_genre') or 'Pending'} | {track.get('subgenre') or '—'} | "
            f"{track.get('master_folder') or '—'} | "
            f"{float(track.get('confidence', 0)):.0%} | {flags} | "
            f"{track.get('review_status', 'pending')} |"
        )

    lines.extend(["", "## Evidence and reasoning", ""])
    for index, track in enumerate(research.get("tracks", []), start=1):
        destination = track.get("subgenre") or track.get("main_genre") or "Pending"
        lines.extend(
            [
                f"### {index}. {track.get('artist', '')} — {track.get('title', '')}",
                "",
                f"- Recommendation: **{destination}**",
                f"- Classification level: {track.get('classification_level') or '—'}",
                f"- Main genre: {track.get('main_genre') or '—'}",
                f"- Master folder: {track.get('master_folder') or '—'}",
                f"- Confidence: {float(track.get('confidence', 0)):.0%}",
                f"- Review flags: {', '.join(track.get('review_flags', [])) or 'None'}",
                f"- Rationale: {track.get('rationale') or '_Codex research pending._'}",
            ]
        )
        evidence = track.get("evidence", [])
        if evidence:
            for item in evidence:
                url = _source_url(item)
                label = _source_label(item)
                scope = item.get("scope", "source") if isinstance(item, dict) else "source"
                rendered = f"[{label}]({url})" if url else label
                lines.append(f"- Evidence ({scope}): {rendered}")
        else:
            lines.append("- Evidence: _Codex research pending._")
        lines.append("")

    flagged = [track for track in research.get("tracks", []) if track.get("review_flags")]
    lines.extend(["## Review-flagged classifications", ""])
    if flagged:
        for track in flagged:
            lines.append(
                f"- {track.get('artist', '')} — {track.get('title', '')}: "
                f"{', '.join(track.get('review_flags', []))}"
            )
    else:
        lines.append("_None._")
    lines.append("")

    already_correct = [
        track for track in research.get("tracks", [])
        if track.get("already_in_target_playlist")
    ]
    lines.extend(["## Tracks already in the correct target playlist", ""])
    if already_correct:
        for track in already_correct:
            lines.append(
                f"- {track.get('artist', '')} — {track.get('title', '')} "
                f"(`{track.get('target_playlist_id')}`)"
            )
    else:
        lines.append("_None identified._")
    lines.append("")

    lines.extend(["## Proposed main genres", ""])
    proposals = research.get("proposed_main_genres", [])
    if not proposals:
        lines.append("_None._")
    for proposal in proposals:
        lines.extend(
            [
                f"### {proposal.get('name', 'Unnamed proposal')}",
                "",
                f"- Aliases: {', '.join(proposal.get('aliases', [])) or '—'}",
                f"- Rationale: {proposal.get('rationale', '—')}",
                f"- Placements: {', '.join(p.get('master_folder', '') for p in proposal.get('placements', [])) or '—'}",
                "",
            ]
        )

    lines.extend(["## Proposed subgenres", ""])
    proposals = research.get("proposed_subgenres", [])
    if not proposals:
        lines.append("_None._")
    for proposal in proposals:
        lines.extend(
            [
                f"### {proposal.get('name', 'Unnamed proposal')}",
                "",
                f"- Main genre: {proposal.get('main_genre', '—')}",
                f"- Master folder: {proposal.get('master_folder', '—')}",
                f"- Aliases: {', '.join(proposal.get('aliases', [])) or '—'}",
                f"- BPM justification: {proposal.get('bpm_justification', '—')}",
                f"- Rationale: {proposal.get('rationale', '—')}",
                f"- Affected track IDs: {', '.join(proposal.get('affected_track_ids', [])) or '—'}",
                "",
            ]
        )

    lines.extend(["## Proposed aliases and routing changes", ""])
    aliases = research.get("proposed_aliases", [])
    routes = research.get("proposed_routing_changes", [])
    if not aliases and not routes:
        lines.append("_None._")
    for alias in aliases:
        lines.append(
            f"- Alias `{alias.get('alias')}` → {alias.get('canonical_type')} "
            f"`{alias.get('canonical_name')}`: {alias.get('rationale', '—')}"
        )
    for route in routes:
        lines.append(
            f"- Route {route.get('classification_level')} `{route.get('name')}` "
            f"in {route.get('master_folder')}: {route.get('rationale', '—')}"
        )
    return "\n".join(lines).rstrip() + "\n"


def render_research_markdown(research: dict) -> str:
    """Render the provisional/reviewed JSON as a readable cited Markdown report."""
    if int(research.get("artifact_version", 1)) >= 2:
        return _render_research_markdown_v2(research)
    summary = research.get("summary", {})
    lines = [
        f"# Genre Research: {research.get('source_playlist_name', 'Spotify playlist')}",
        "",
        f"- Status: **{research.get('status', 'provisional')}**",
        f"- Spotify playlist ID: `{research.get('source_playlist_id', '')}`",
        f"- Tracks: {research.get('total_tracks', len(research.get('tracks', [])))}",
        f"- Uncertain: {summary.get('uncertain', 0)}",
        f"- Proposed schema additions: {summary.get('proposed_schema_additions', 0)}",
        "",
        "## Classification summary",
        "",
        "| Subgenre | Tracks |",
        "|---|---:|",
    ]
    by_subgenre = summary.get("by_subgenre", {})
    if by_subgenre:
        for subgenre, count in sorted(by_subgenre.items()):
            lines.append(f"| {subgenre} | {count} |")
    else:
        lines.append("| _No recommendations yet_ | 0 |")

    lines.extend(
        [
            "",
            "## Track review",
            "",
            "| # | Track | Album / release | Recommendation | Confidence | Status |",
            "|---:|---|---|---|---:|---|",
        ]
    )
    for index, track in enumerate(research.get("tracks", []), start=1):
        recommendation = track.get("subgenre") or "Uncertain"
        confidence = f"{float(track.get('confidence', 0)):.0%}"
        title = f"{track.get('artist', '')} — {track.get('title', '')}"
        album = track.get("album") or "—"
        lines.append(
            f"| {index} | {title} | {album} | {recommendation} | "
            f"{confidence} | {track.get('review_status', 'uncertain')} |"
        )

    lines.extend(["", "## Evidence and reasoning", ""])
    for index, track in enumerate(research.get("tracks", []), start=1):
        lines.extend(
            [
                f"### {index}. {track.get('artist', '')} — {track.get('title', '')}",
                "",
                f"- Recommendation: **{track.get('subgenre') or 'Uncertain'}**",
                f"- Master folder: {track.get('master_folder') or '—'}",
                f"- Confidence: {float(track.get('confidence', 0)):.0%}",
                f"- Rationale: {track.get('rationale') or '_Research pending._'}",
            ]
        )
        sources = track.get("sources", [])
        if sources:
            source_links = []
            for source in sources:
                url = _source_url(source)
                label = _source_label(source)
                source_links.append(f"[{label}]({url})" if url else label)
            lines.append(f"- Sources: {', '.join(source_links)}")
        else:
            lines.append("- Sources: _None yet._")
        lines.append("")

    uncertain_tracks = [
        track
        for track in research.get("tracks", [])
        if track.get("review_status") == "uncertain"
    ]
    lines.extend(["## Uncertain tracks", ""])
    if uncertain_tracks:
        for track in uncertain_tracks:
            lines.append(
                f"- {track.get('artist', '')} — {track.get('title', '')}: "
                f"{track.get('rationale') or 'insufficient track-specific evidence'}"
            )
    else:
        lines.append("_None._")
    lines.append("")

    already_correct = [
        track
        for track in research.get("tracks", [])
        if track.get("already_in_target_playlist")
    ]
    lines.extend(["## Tracks already in the correct target playlist", ""])
    if already_correct:
        for track in already_correct:
            lines.append(
                f"- {track.get('artist', '')} — {track.get('title', '')} "
                f"(`{track.get('target_playlist_id')}`)"
            )
    else:
        lines.append("_None identified._")
    lines.append("")

    proposals = research.get("proposed_schema_additions", [])
    lines.extend(["## Proposed schema additions", ""])
    if not proposals:
        lines.append("_None._")
    for proposal in proposals:
        lines.extend(
            [
                f"### {proposal.get('name', 'Unnamed proposal')}",
                "",
                f"- Master folder: {proposal.get('master_folder', '—')}",
                f"- BPM justification: {proposal.get('bpm_justification', '—')}",
                f"- Rationale: {proposal.get('rationale', '—')}",
                f"- Affected track IDs: {', '.join(proposal.get('affected_track_ids', [])) or '—'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# PART 1B — validate and approve researched recommendations
# ---------------------------------------------------------------------------


def refresh_research_summary(research: dict, schema: dict) -> dict:
    """Recompute target mappings and all derived summary counts in place."""
    if int(research.get("artifact_version", 1)) >= 2:
        by_main: dict[str, int] = {}
        by_subgenre: dict[str, int] = {}
        pending = 0
        parent_fallbacks = 0
        flagged = 0
        already_in_target = 0
        source_playlist_id = research.get("source_playlist_id")
        proposed_main = {
            canonical_key(proposal.get("name", ""))
            for proposal in research.get("proposed_main_genres", [])
        }
        proposed_sub = {
            (
                canonical_key(proposal.get("master_folder", "")),
                canonical_key(proposal.get("main_genre", "")),
                canonical_key(proposal.get("name", "")),
            )
            for proposal in research.get("proposed_subgenres", [])
        }

        for track in research.get("tracks", []):
            main_genre = track.get("main_genre")
            subgenre = track.get("subgenre")
            master_folder = track.get("master_folder")
            if not main_genre or not master_folder:
                pending += 1
                track["target_playlist_id"] = None
                track["target_kind"] = None
                track["already_in_target_playlist"] = False
                continue

            by_main[main_genre] = by_main.get(main_genre, 0) + 1
            destination_name = subgenre or f"{main_genre} (parent)"
            by_subgenre[destination_name] = by_subgenre.get(destination_name, 0) + 1
            if not subgenre:
                parent_fallbacks += 1
            if track.get("review_flags"):
                flagged += 1

            destination = resolve_destination(
                schema,
                master_folder=master_folder,
                main_genre=main_genre,
                subgenre=subgenre,
            )
            is_proposed = canonical_key(main_genre) in proposed_main or (
                canonical_key(master_folder),
                canonical_key(main_genre),
                canonical_key(subgenre or ""),
            ) in proposed_sub
            target_id = None if is_proposed or not destination else destination.get(
                "spotify_playlist"
            )
            track["target_playlist_id"] = target_id
            track["target_kind"] = "subgenre" if subgenre else "main_genre"
            track["already_in_target_playlist"] = bool(
                target_id and target_id == source_playlist_id
            )
            if track["already_in_target_playlist"]:
                already_in_target += 1

        research["total_tracks"] = len(research.get("tracks", []))
        research["summary"] = {
            "by_main_genre": by_main,
            "by_subgenre": by_subgenre,
            "parent_fallbacks": parent_fallbacks,
            "pending_classification": pending,
            "review_flagged": flagged,
            "proposed_main_genres": len(research.get("proposed_main_genres", [])),
            "proposed_subgenres": len(research.get("proposed_subgenres", [])),
            "already_in_correct_playlist": already_in_target,
        }
        return research

    subgenres, _ = _schema_indexes(schema)
    by_subgenre: dict[str, int] = {}
    uncertain = 0
    already_in_target = 0
    source_playlist_id = research.get("source_playlist_id")

    for track in research.get("tracks", []):
        subgenre = track.get("subgenre")
        if not subgenre:
            uncertain += 1
            track["target_playlist_id"] = None
            track["already_in_target_playlist"] = False
            continue

        by_subgenre[subgenre] = by_subgenre.get(subgenre, 0) + 1
        schema_entry = subgenres.get(subgenre)
        target_id = schema_entry.get("spotify_playlist") if schema_entry else None
        track["target_playlist_id"] = target_id
        track["already_in_target_playlist"] = bool(
            target_id and target_id == source_playlist_id
        )
        if track["already_in_target_playlist"]:
            already_in_target += 1

    research["total_tracks"] = len(research.get("tracks", []))
    research["summary"] = {
        "by_subgenre": by_subgenre,
        "uncertain": uncertain,
        "proposed_schema_additions": len(
            research.get("proposed_schema_additions", [])
        ),
        "already_in_correct_playlist": already_in_target,
    }
    return research


def _validate_research_v2(research: dict, schema: dict) -> None:
    """Validate a complete, zero-undetermined Codex research artifact."""
    errors: list[str] = []
    if not is_v2(schema):
        errors.append("artifact v2 requires schema v2")
    else:
        try:
            validate_schema(schema)
        except ValueError as exc:
            errors.append(str(exc))
    if research.get("artifact_type") != "spotify_genre_research":
        errors.append("artifact_type must be 'spotify_genre_research'")
    if research.get("status") != "provisional":
        errors.append("research status must be 'provisional'")

    tracks = research.get("tracks")
    if not isinstance(tracks, list):
        raise ValueError("Research validation failed:\n- tracks must be a list")
    if research.get("total_tracks") != len(tracks):
        errors.append("total_tracks does not match the tracks array")

    masters = {canonical_key(m["name"]): m for m in schema.get("master_folders", [])}
    existing_main = main_genre_index(schema)
    existing_sub = subgenre_index(schema)
    occupied_labels: dict[str, str] = {}
    for genre in schema.get("main_genres", []):
        for label in [genre["name"], *genre.get("aliases", [])]:
            occupied_labels[canonical_key(label)] = f"main genre {genre['name']}"
    for context in iter_subgenres(schema):
        subgenre = context["subgenre"]
        for label in [subgenre["name"], *subgenre.get("aliases", [])]:
            occupied_labels[canonical_key(label)] = f"subgenre {subgenre['name']}"

    proposed_main: dict[str, dict] = {}
    proposed_main_ids: set[str] = set()
    for proposal in research.get("proposed_main_genres", []):
        name = proposal.get("name", "")
        key = canonical_key(name)
        proposal_id = proposal.get("id") or taxonomy_slug(name)
        if not name or not proposal.get("rationale"):
            errors.append("every proposed main genre requires name and rationale")
            continue
        if key in occupied_labels:
            errors.append(f"proposed main genre {name!r} duplicates {occupied_labels[key]}")
        if key in proposed_main or proposal_id in proposed_main_ids:
            errors.append(f"duplicate proposed main genre {name!r}")
        proposed_main[key] = proposal
        proposed_main_ids.add(proposal_id)
        placements = proposal.get("placements", [])
        if not placements:
            errors.append(f"proposed main genre {name!r} requires a placement")
        for placement in placements:
            if canonical_key(placement.get("master_folder", "")) not in masters:
                errors.append(
                    f"proposed main genre {name!r} references unknown master folder"
                )
            if not placement.get("bpm_justification"):
                errors.append(
                    f"proposed main genre {name!r} requires BPM justification"
                )
        for alias in proposal.get("aliases", []):
            alias_key = canonical_key(alias)
            if alias_key in occupied_labels or alias_key in proposed_main:
                errors.append(f"proposed alias {alias!r} duplicates an existing label")
            occupied_labels[alias_key] = f"proposed main genre {name}"
        occupied_labels[key] = f"proposed main genre {name}"

    proposed_sub: dict[tuple[str, str, str], dict] = {}
    proposed_sub_ids: set[str] = set()
    for proposal in research.get("proposed_subgenres", []):
        name = proposal.get("name", "")
        main_name = proposal.get("main_genre", "")
        master_name = proposal.get("master_folder", "")
        key = (
            canonical_key(master_name),
            canonical_key(main_name),
            canonical_key(name),
        )
        proposal_id = proposal.get("id") or taxonomy_slug(name)
        if not all([name, main_name, master_name]):
            errors.append("every proposed subgenre requires name, main_genre, master_folder")
            continue
        if canonical_key(master_name) not in masters:
            errors.append(f"proposed subgenre {name!r} references unknown master folder")
        if canonical_key(main_name) not in existing_main and canonical_key(
            main_name
        ) not in proposed_main:
            errors.append(f"proposed subgenre {name!r} references unknown main genre")
        if canonical_key(name) in occupied_labels:
            errors.append(f"proposed subgenre {name!r} duplicates {occupied_labels[canonical_key(name)]}")
        if key in proposed_sub or proposal_id in proposed_sub_ids:
            errors.append(f"duplicate proposed subgenre {name!r}")
        if not proposal.get("bpm_justification") or not proposal.get("rationale"):
            errors.append(
                f"proposed subgenre {name!r} requires BPM justification and rationale"
            )
        proposed_sub[key] = proposal
        proposed_sub_ids.add(proposal_id)
        for alias in proposal.get("aliases", []):
            alias_key = canonical_key(alias)
            if alias_key in occupied_labels:
                errors.append(f"proposed alias {alias!r} duplicates an existing label")
            occupied_labels[alias_key] = f"proposed subgenre {name}"
        occupied_labels[canonical_key(name)] = f"proposed subgenre {name}"

    # Alias proposals add lookup labels to an existing/proposed canonical node.
    proposed_alias_keys: set[str] = set()
    for proposal in research.get("proposed_aliases", []):
        alias = proposal.get("alias", "")
        alias_key = canonical_key(alias)
        canonical_type = proposal.get("canonical_type")
        canonical_name = proposal.get("canonical_name", "")
        if canonical_type not in {"main_genre", "subgenre"}:
            errors.append(f"alias {alias!r} has invalid canonical_type")
        canonical_exists = (
            canonical_key(canonical_name) in existing_main
            or canonical_key(canonical_name) in existing_sub
            or canonical_key(canonical_name) in proposed_main
            or any(key[2] == canonical_key(canonical_name) for key in proposed_sub)
        )
        if not alias or not canonical_exists or not proposal.get("rationale"):
            errors.append(
                f"alias proposal {alias!r} requires an existing canonical target and rationale"
            )
        if alias_key in occupied_labels or alias_key in proposed_alias_keys:
            errors.append(f"alias proposal {alias!r} duplicates an existing label")
        proposed_alias_keys.add(alias_key)

    seen_ids: set[str] = set()
    proposed_main_track_ids: set[str] = set()
    proposed_sub_track_ids: set[str] = set()
    for index, track in enumerate(tracks, start=1):
        prefix = f"track {index} ({track.get('artist', '')} — {track.get('title', '')})"
        track_id = track.get("spotify_track_id")
        if not track_id:
            errors.append(f"{prefix}: missing spotify_track_id")
        elif track_id in seen_ids:
            errors.append(f"{prefix}: duplicate spotify_track_id {track_id!r}")
        else:
            seen_ids.add(track_id)

        status = track.get("review_status")
        if status != "proposed":
            errors.append(f"{prefix}: provisional v2 research requires proposed status")
        main_name = track.get("main_genre")
        master_name = track.get("master_folder")
        sub_name = track.get("subgenre")
        level = track.get("classification_level")
        if not main_name or not master_name:
            errors.append(f"{prefix}: every valid track requires main_genre and master_folder")
            continue
        if canonical_key(master_name) not in masters:
            errors.append(f"{prefix}: unknown master folder {master_name!r}")
        main_is_proposed = canonical_key(main_name) in proposed_main
        if canonical_key(main_name) not in existing_main and not main_is_proposed:
            errors.append(f"{prefix}: unknown main genre {main_name!r}")
        if level not in {"main_genre", "subgenre"}:
            errors.append(f"{prefix}: invalid classification_level {level!r}")
        if level == "main_genre" and sub_name is not None:
            errors.append(f"{prefix}: main_genre classification must have null subgenre")
        if level == "subgenre" and not sub_name:
            errors.append(f"{prefix}: subgenre classification requires subgenre")

        sub_key = (
            canonical_key(master_name),
            canonical_key(main_name),
            canonical_key(sub_name or ""),
        )
        sub_is_proposed = sub_key in proposed_sub
        if level == "subgenre" and not sub_is_proposed:
            destination = resolve_destination(
                schema,
                master_folder=master_name,
                main_genre=main_name,
                subgenre=sub_name,
            )
            if not destination:
                errors.append(
                    f"{prefix}: subgenre does not belong to the selected main/master placement"
                )
        if level == "main_genre" and not main_is_proposed:
            destination = resolve_destination(
                schema,
                master_folder=master_name,
                main_genre=main_name,
                subgenre=None,
            )
            if not destination:
                errors.append(
                    f"{prefix}: main genre has no placement in the selected master folder"
                )

        try:
            confidence = float(track.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = -1
        if not 0 <= confidence <= 1:
            errors.append(f"{prefix}: confidence must be between 0 and 1")
        if not track.get("rationale"):
            errors.append(f"{prefix}: classification requires a rationale")
        flags = track.get("review_flags", [])
        invalid_flags = set(flags) - _REVIEW_FLAGS
        if invalid_flags:
            errors.append(f"{prefix}: invalid review flags {sorted(invalid_flags)!r}")
        evidence = track.get("evidence", [])
        allowed_scopes = {"track", "release", "artist", "metadata", "inference"}
        invalid_scopes = [
            item.get("scope")
            for item in evidence
            if not isinstance(item, dict) or item.get("scope") not in allowed_scopes
        ]
        if invalid_scopes:
            errors.append(
                f"{prefix}: evidence requires a valid scope "
                f"(track, release, artist, metadata, or inference)"
            )
        valid_urls = [
            _source_url(item)
            for item in evidence
            if _source_url(item)
            and urlparse(_source_url(item)).scheme in {"http", "https"}
            and urlparse(_source_url(item)).netloc
        ]
        if not valid_urls:
            errors.append(f"{prefix}: classification requires direct HTTP(S) evidence")
        direct_recording_evidence = any(
            isinstance(item, dict) and item.get("scope") in {"track", "release"}
            for item in evidence
        )
        if confidence >= 0.75 and not direct_recording_evidence:
            errors.append(
                f"{prefix}: confidence >= 75% requires track- or release-scoped evidence"
            )
        if not direct_recording_evidence and not (
            {"artist-only", "broad-source", "inferred-from-metadata"} & set(flags)
        ):
            errors.append(
                f"{prefix}: weaker evidence requires an artist-only, broad-source, "
                "or inferred-from-metadata review flag"
            )
        if track.get("is_new_main_genre_proposal") != main_is_proposed:
            errors.append(f"{prefix}: main-genre proposal marker is inconsistent")
        if track.get("is_new_subgenre_proposal") != sub_is_proposed:
            errors.append(f"{prefix}: subgenre proposal marker is inconsistent")
        if main_is_proposed and track_id:
            proposed_main_track_ids.add(track_id)
        if sub_is_proposed and track_id:
            proposed_sub_track_ids.add(track_id)

    declared_main_ids = {
        track_id
        for proposal in research.get("proposed_main_genres", [])
        for track_id in proposal.get("affected_track_ids", [])
    }
    declared_sub_ids = {
        track_id
        for proposal in research.get("proposed_subgenres", [])
        for track_id in proposal.get("affected_track_ids", [])
    }
    if proposed_main_track_ids != declared_main_ids:
        errors.append("main-genre affected_track_ids must exactly match marked tracks")
    if proposed_sub_track_ids != declared_sub_ids:
        errors.append("subgenre affected_track_ids must exactly match marked tracks")

    for index, change in enumerate(
        research.get("proposed_routing_changes", []), start=1
    ):
        prefix = f"routing change {index}"
        level = change.get("classification_level")
        if level not in {"main_genre", "subgenre"}:
            errors.append(f"{prefix}: invalid classification_level {level!r}")
            continue
        if not change.get("name") or not change.get("master_folder"):
            errors.append(f"{prefix}: name and master_folder are required")
            continue
        if "local_folder" not in change and "spotify_playlist" not in change:
            errors.append(
                f"{prefix}: requires local_folder or spotify_playlist change"
            )
        if not change.get("rationale"):
            errors.append(f"{prefix}: rationale is required")
        if level == "subgenre" and not change.get("main_genre"):
            errors.append(f"{prefix}: subgenre routing requires main_genre")
        main_name = (
            change.get("name") if level == "main_genre" else change.get("main_genre")
        )
        sub_name = change.get("name") if level == "subgenre" else None
        destination_exists = resolve_destination(
            schema,
            master_folder=change.get("master_folder", ""),
            main_genre=main_name or "",
            subgenre=sub_name,
        )
        proposed_destination = (
            level == "main_genre" and canonical_key(main_name or "") in proposed_main
        ) or (
            level == "subgenre"
            and (
                canonical_key(change.get("master_folder", "")),
                canonical_key(main_name or ""),
                canonical_key(sub_name or ""),
            )
            in proposed_sub
        )
        if not destination_exists and not proposed_destination:
            errors.append(f"{prefix}: target is not present or proposed")

    if errors:
        raise ValueError("Research validation failed:\n- " + "\n- ".join(errors))


def validate_research(research: dict, schema: dict) -> None:
    """Raise ValueError when a researched artifact is unsafe to approve."""
    if int(research.get("artifact_version", 1)) >= 2:
        _validate_research_v2(research, schema)
        return
    errors: list[str] = []
    if research.get("artifact_type") != "spotify_genre_research":
        errors.append("artifact_type must be 'spotify_genre_research'")
    if research.get("status") != "provisional":
        errors.append("research status must be 'provisional'")

    tracks = research.get("tracks")
    if not isinstance(tracks, list):
        raise ValueError("Research validation failed:\n- tracks must be a list")
    if research.get("total_tracks") != len(tracks):
        errors.append("total_tracks does not match the tracks array")

    subgenres, master_folders = _schema_indexes(schema)
    proposals = research.get("proposed_schema_additions", [])
    proposal_by_name: dict[str, dict] = {}
    for proposal in proposals:
        name = proposal.get("name")
        folder_name = proposal.get("master_folder")
        if not name:
            errors.append("every schema proposal requires a name")
            continue
        if name in subgenres:
            errors.append(f"proposed subgenre {name!r} already exists in the schema")
        if name in proposal_by_name:
            errors.append(f"duplicate schema proposal: {name!r}")
        proposal_by_name[name] = proposal
        if folder_name not in master_folders:
            errors.append(
                f"proposal {name!r} references unknown master folder {folder_name!r}"
            )
        if not proposal.get("bpm_justification"):
            errors.append(f"proposal {name!r} requires BPM justification")
        if not proposal.get("rationale"):
            errors.append(f"proposal {name!r} requires a rationale")

    seen_ids: set[str] = set()
    for index, track in enumerate(tracks, start=1):
        prefix = f"track {index} ({track.get('artist', '')} — {track.get('title', '')})"
        track_id = track.get("spotify_track_id")
        if not track_id:
            errors.append(f"{prefix}: missing spotify_track_id")
        elif track_id in seen_ids:
            errors.append(f"{prefix}: duplicate spotify_track_id {track_id!r}")
        else:
            seen_ids.add(track_id)

        status = track.get("review_status")
        if status not in _RESEARCH_STATUSES:
            errors.append(f"{prefix}: invalid review_status {status!r}")

        try:
            confidence = float(track.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = -1
        if not 0 <= confidence <= 1:
            errors.append(f"{prefix}: confidence must be between 0 and 1")

        subgenre = track.get("subgenre")
        if status in {"uncertain", "rejected"}:
            if subgenre is not None:
                errors.append(f"{prefix}: {status} tracks must not force a subgenre")
            continue

        if not subgenre:
            errors.append(f"{prefix}: recommended tracks require a subgenre")
            continue
        if not track.get("rationale"):
            errors.append(f"{prefix}: recommended tracks require a rationale")

        valid_source_urls = [
            url
            for url in (_source_url(source) for source in track.get("sources", []))
            if urlparse(url).scheme in {"http", "https"} and urlparse(url).netloc
        ]
        if not valid_source_urls:
            errors.append(
                f"{prefix}: recommended tracks require a direct HTTP(S) source URL"
            )

        is_proposal = bool(track.get("is_new_subgenre_proposal"))
        if is_proposal:
            proposal = proposal_by_name.get(subgenre)
            if not proposal:
                errors.append(
                    f"{prefix}: proposed subgenre {subgenre!r} has no proposal entry"
                )
            elif track.get("master_folder") != proposal.get("master_folder"):
                errors.append(f"{prefix}: master folder does not match its proposal")
        else:
            schema_entry = subgenres.get(subgenre)
            if not schema_entry:
                errors.append(f"{prefix}: unknown schema subgenre {subgenre!r}")
            else:
                if track.get("master_folder") != schema_entry["master_folder"]:
                    errors.append(f"{prefix}: master folder does not match the schema")
                supplied_target = track.get("target_playlist_id")
                expected_target = schema_entry.get("spotify_playlist")
                if supplied_target not in {None, expected_target}:
                    errors.append(f"{prefix}: target playlist does not match the schema")

    proposal_track_ids = {
        track["spotify_track_id"]
        for track in tracks
        if track.get("is_new_subgenre_proposal") and track.get("spotify_track_id")
    }
    declared_proposal_ids = {
        track_id
        for proposal in proposals
        for track_id in proposal.get("affected_track_ids", [])
    }
    if proposal_track_ids != declared_proposal_ids:
        errors.append(
            "proposal affected_track_ids must exactly match tracks marked as proposals"
        )

    if errors:
        raise ValueError("Research validation failed:\n- " + "\n- ".join(errors))


def _proposal_local_folder(master_folder: dict, subgenre_name: str) -> str:
    """Derive a local folder path consistent with sibling schema entries."""
    siblings = master_folder.get("subgenres", [])
    if siblings and siblings[0].get("local_folder"):
        parent = Path(siblings[0]["local_folder"]).parent
    else:
        parent = Path(master_folder.get("local_path", "music")) / _slugify(
            master_folder["name"]
        )
    return str(parent / subgenre_name.lower())


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _research_run_id(research: dict) -> str:
    prepared = str(research.get("prepared_at", ""))
    compact = re.sub(r"[^0-9]", "", prepared)[:14]
    return f"{compact or datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}Z"


def _find_main_genre(schema: dict, name: str) -> dict | None:
    return main_genre_index(schema).get(canonical_key(name))


def _find_placement(schema: dict, master_name: str, main_genre_id: str) -> dict | None:
    for context in iter_genre_placements(schema):
        if (
            canonical_key(context["master_folder"]["name"]) == canonical_key(master_name)
            and context["placement"].get("main_genre_id") == main_genre_id
        ):
            return context["placement"]
    return None


def _apply_v2_taxonomy_proposals(research: dict, schema: dict) -> dict:
    """Return a schema copy containing every reviewed taxonomy/routing proposal."""
    updated = json.loads(json.dumps(schema))
    masters = {
        canonical_key(master["name"]): master
        for master in updated.get("master_folders", [])
    }

    for proposal in research.get("proposed_main_genres", []):
        genre = {
            "id": proposal.get("id") or taxonomy_slug(proposal["name"]),
            "name": proposal["name"],
            "aliases": proposal.get("aliases", []),
            "spotify_playlist": None,
            "notes": proposal.get("notes") or proposal["rationale"],
        }
        updated["main_genres"].append(genre)
        for placement_proposal in proposal["placements"]:
            master = masters[canonical_key(placement_proposal["master_folder"])]
            master["genre_placements"].append(
                {
                    "main_genre_id": genre["id"],
                    "local_folder": str(
                        Path(master["local_path"]) / canonical_key(genre["name"])
                    ),
                    "migration_status": "complete",
                    "subgenres": [],
                }
            )

    for proposal in research.get("proposed_subgenres", []):
        genre = _find_main_genre(updated, proposal["main_genre"])
        if genre is None:  # guarded by validation
            raise ValueError(f"Unknown proposed parent {proposal['main_genre']!r}")
        placement = _find_placement(
            updated, proposal["master_folder"], genre["id"]
        )
        if placement is None:  # guarded by validation
            raise ValueError(
                f"No placement for {proposal['main_genre']!r} in "
                f"{proposal['master_folder']!r}"
            )
        placement["subgenres"].append(
            {
                "id": proposal.get("id") or taxonomy_slug(proposal["name"]),
                "name": proposal["name"],
                "aliases": proposal.get("aliases", []),
                "local_folder": str(
                    Path(placement["local_folder"]) / canonical_key(proposal["name"])
                ),
                "migration_status": "complete",
                "spotify_playlist": None,
                "notes": proposal.get("notes") or proposal["rationale"],
            }
        )

    for proposal in research.get("proposed_aliases", []):
        canonical_name = proposal["canonical_name"]
        if proposal["canonical_type"] == "main_genre":
            node = _find_main_genre(updated, canonical_name)
        else:
            context = subgenre_index(updated).get(canonical_key(canonical_name))
            node = context["subgenre"] if context else None
        if node is None:  # guarded by validation
            raise ValueError(f"Unknown alias target {canonical_name!r}")
        node.setdefault("aliases", []).append(proposal["alias"])

    for change in research.get("proposed_routing_changes", []):
        destination = resolve_destination(
            updated,
            master_folder=change["master_folder"],
            main_genre=(
                change["name"]
                if change["classification_level"] == "main_genre"
                else change["main_genre"]
            ),
            subgenre=(
                change["name"]
                if change["classification_level"] == "subgenre"
                else None
            ),
        )
        if destination is None:
            raise ValueError(f"Unknown routing-change target {change!r}")
        if "local_folder" in change:
            destination["node"]["local_folder"] = change["local_folder"]
        if "spotify_playlist" in change:
            destination["playlist_node"]["spotify_playlist"] = change[
                "spotify_playlist"
            ]

    updated["_meta"]["last_updated"] = datetime.now(timezone.utc).date().isoformat()
    validate_schema(updated)
    return updated


def _approve_research_v2(
    research: dict,
    *,
    source_path: Path | None,
    schema: dict,
    schema_path: Path,
    output_path: Path,
    history_root: Path | None,
) -> dict:
    validate_research(research, schema)
    updated_schema = _apply_v2_taxonomy_proposals(research, schema)

    run_root = history_root or output_path.parent / "research" / "module_06"
    run_dir = (
        Path(run_root)
        / _slugify(research.get("source_playlist_name", "playlist"))
        / _research_run_id(research)
    )
    if run_dir.exists():
        raise FileExistsError(f"Immutable review history already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=False)

    provisional_json = run_dir / "provisional.json"
    provisional_markdown = run_dir / "provisional.md"
    if source_path is not None:
        shutil.copyfile(source_path, provisional_json)
        source_markdown = source_path.with_suffix(".md")
        if source_markdown.exists():
            shutil.copyfile(source_markdown, provisional_markdown)
        else:
            provisional_markdown.write_text(
                render_research_markdown(research), encoding="utf-8"
            )
    else:
        provisional_json.write_text(
            json.dumps(research, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        provisional_markdown.write_text(
            render_research_markdown(research), encoding="utf-8"
        )

    reviewed = json.loads(json.dumps(research))
    reviewed["status"] = "approved"
    reviewed["approved_at"] = datetime.now(timezone.utc).isoformat()
    for track in reviewed["tracks"]:
        track["review_status"] = "approved"
    refresh_research_summary(reviewed, updated_schema)

    reviewed_json = run_dir / "reviewed.json"
    reviewed_markdown = run_dir / "reviewed.md"
    reviewed_json.write_text(
        json.dumps(reviewed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    reviewed_markdown.write_text(
        render_research_markdown(reviewed), encoding="utf-8"
    )

    approved_tracks = []
    for track in reviewed["tracks"]:
        approved_tracks.append(
            {
                "spotify_track_id": track["spotify_track_id"],
                "artist": track.get("artist", ""),
                "title": track.get("title", ""),
                "approved": True,
                "main_genre": track["main_genre"],
                "subgenre": track.get("subgenre"),
                "classification_level": track["classification_level"],
                "master_folder": track["master_folder"],
                "confidence": round(float(track.get("confidence", 0)), 4),
                "review_flags": track.get("review_flags", []),
                "target_kind": track["target_kind"],
                "target_playlist_id": track.get("target_playlist_id"),
                "evidence": track.get("evidence", []),
                "research_rationale": track.get("rationale", ""),
            }
        )

    try:
        review_history = str(run_dir.relative_to(output_path.parent))
    except ValueError:
        review_history = str(run_dir)

    output = {
        "artifact_type": "spotify_classify_results",
        "artifact_version": 2,
        "source_playlist_id": reviewed["source_playlist_id"],
        "source_playlist_name": reviewed.get(
            "source_playlist_name", reviewed["source_playlist_id"]
        ),
        "classified_at": reviewed["approved_at"],
        "classification_method": "codex_web_research",
        "review_history": review_history,
        "total_tracks": len(approved_tracks),
        "tracks": approved_tracks,
    }

    output_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    save_schema(updated_schema, schema_path)

    manifest = {
        "artifact_type": "module_06_approval",
        "artifact_version": 1,
        "approved_at": reviewed["approved_at"],
        "reviewed_by": ["codex", "user"],
        "source_playlist_id": reviewed["source_playlist_id"],
        "provisional_json_sha256": _sha256_bytes(provisional_json.read_bytes()),
        "provisional_markdown_sha256": _sha256_bytes(
            provisional_markdown.read_bytes()
        ),
        "reviewed_json_sha256": _sha256_bytes(reviewed_json.read_bytes()),
        "reviewed_markdown_sha256": _sha256_bytes(reviewed_markdown.read_bytes()),
        "classify_results_sha256": _sha256_bytes(output_path.read_bytes()),
        "applied_taxonomy": {
            "main_genres": [p["name"] for p in research.get("proposed_main_genres", [])],
            "subgenres": [p["name"] for p in research.get("proposed_subgenres", [])],
            "aliases": [p["alias"] for p in research.get("proposed_aliases", [])],
            "routing_changes": len(research.get("proposed_routing_changes", [])),
        },
    }
    (run_dir / "approval_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Approved Part 2 input written to {output_path}")
    print(f"Immutable review history written to {run_dir}")
    return output


def approve_research(
    research_or_path: dict | str | Path,
    *,
    schema: dict | None = None,
    schema_path: str | Path = DEFAULT_SCHEMA_PATH,
    output_path: str | Path = DEFAULT_RESULTS_PATH,
    history_root: str | Path | None = None,
) -> dict:
    """Approve recommendations, apply proposals, and write Part 2 input.

    This function performs no Spotify API calls.
    """
    source_path = Path(research_or_path) if isinstance(research_or_path, (str, Path)) else None
    if source_path is not None:
        with open(source_path, encoding="utf-8") as file:
            research: dict = json.load(file)
    else:
        # Avoid mutating a caller-owned research object.
        research = json.loads(json.dumps(research_or_path))

    if schema is None:
        schema = load_schema(Path(schema_path))
    if int(research.get("artifact_version", 1)) >= 2:
        return _approve_research_v2(
            research,
            source_path=source_path,
            schema=schema,
            schema_path=Path(schema_path),
            output_path=Path(output_path),
            history_root=Path(history_root) if history_root else None,
        )
    validate_research(research, schema)

    _, master_folders = _schema_indexes(schema)
    additions = research.get("proposed_schema_additions", [])
    for proposal in additions:
        folder = master_folders[proposal["master_folder"]]
        folder["subgenres"].append(
            {
                "name": proposal["name"],
                "local_folder": _proposal_local_folder(folder, proposal["name"]),
                "spotify_playlist": None,
                "notes": proposal.get("notes") or (
                    f"Web-research proposal approved: {proposal['rationale']}"
                ),
            }
        )

    if additions:
        schema.setdefault("_meta", {})["last_updated"] = datetime.now(
            timezone.utc
        ).date().isoformat()
        save_schema(schema, Path(schema_path))

    subgenres, _ = _schema_indexes(schema)
    approved_tracks: list[dict] = []
    for track in research["tracks"]:
        status = track.get("review_status")
        subgenre = track.get("subgenre") if status in {"recommended", "approved"} else None
        schema_entry = subgenres.get(subgenre) if subgenre else None
        approved_tracks.append(
            {
                "spotify_track_id": track["spotify_track_id"],
                "artist": track.get("artist", ""),
                "title": track.get("title", ""),
                "subgenre": subgenre,
                "confidence": round(float(track.get("confidence", 0)), 4),
                "is_broad_fallback": False,
                "target_playlist_id": (
                    schema_entry.get("spotify_playlist") if schema_entry else None
                ),
                "evidence": [
                    {
                        "provider": _source_label(source),
                        "genres": [subgenre] if subgenre else [],
                        "url": _source_url(source),
                    }
                    for source in track.get("sources", [])
                ],
                "research_rationale": track.get("rationale", ""),
                "research_review_status": status,
            }
        )

    output = {
        "source_playlist_id": research["source_playlist_id"],
        "source_playlist_name": research.get(
            "source_playlist_name", research["source_playlist_id"]
        ),
        "classified_at": datetime.now(timezone.utc).isoformat(),
        "classification_method": "human_web_research",
        "total_tracks": len(approved_tracks),
        "tracks": approved_tracks,
    }
    output_file = Path(output_path)
    output_file.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Approved Part 2 input written to {output_file}")
    if additions:
        print(f"Applied {len(additions)} schema addition(s) to {schema_path}")
    return output


# ---------------------------------------------------------------------------
# PART 2 — sort_tracks
# ---------------------------------------------------------------------------


def sort_tracks(
    results_or_path: dict | str | Path,
    *,
    env: dict | None = None,
    schema: dict | None = None,
    min_confidence: float = 0.5,
    dry_run: bool = False,
    remove_from_source: bool = True,
) -> dict:
    """Move classified tracks into their subgenre playlists on Spotify.

    Only tracks that satisfy ALL of the following are moved:
    • ``subgenre`` is not ``None``
    • ``is_broad_fallback`` is ``False``
    • ``confidence >= min_confidence``
    • ``target_playlist_id`` is not ``None``

    Parameters
    ----------
    results_or_path:
        Either an approved results dict or a path to ``classify_results.json``.
    env, schema:
        Loaded automatically if omitted.
    min_confidence:
        Minimum confidence score (0–1) required to move a track.
    dry_run:
        If ``True``, print what would happen but make zero write API calls.
    remove_from_source:
        If ``True`` (default), remove moved tracks from the source playlist.

    Returns
    -------
    A summary dict with keys ``moved``, ``skipped`` (sub-keyed by reason),
    and ``errors``.
    """
    if env is None:
        env = load_env()

    # Accept dict or path to JSON file
    if isinstance(results_or_path, (str, Path)):
        with open(results_or_path, encoding="utf-8") as f:
            results: dict = json.load(f)
    else:
        results = results_or_path

    source_playlist_id: str = results["source_playlist_id"]
    source_playlist_name: str = results.get("source_playlist_name", source_playlist_id)
    tracks: list[dict] = results.get("tracks", [])
    reviewed_v2 = int(results.get("artifact_version", 1)) >= 2

    # -----------------------------------------------------------------------
    # Filter tracks into buckets
    # -----------------------------------------------------------------------
    to_move: list[dict] = []
    skipped: dict[str, list[str]] = {
        "unclassified": [],
        "not_approved": [],
        "broad_fallback": [],
        "low_confidence": [],
        "no_target_playlist": [],
    }

    for t in tracks:
        label = f"{t['artist']} — {t['title']}"
        if reviewed_v2 and not t.get("approved"):
            skipped["not_approved"].append(label)
        elif reviewed_v2 and not t.get("main_genre"):
            skipped["unclassified"].append(label)
        elif not reviewed_v2 and t["subgenre"] is None:
            skipped["unclassified"].append(label)
        elif not reviewed_v2 and t["is_broad_fallback"]:
            skipped["broad_fallback"].append(label)
        elif not reviewed_v2 and t["confidence"] < min_confidence:
            skipped["low_confidence"].append(label)
        elif t["target_playlist_id"] is None:
            skipped["no_target_playlist"].append(label)
        else:
            to_move.append(t)

    # -----------------------------------------------------------------------
    # Dry-run: just print what would happen
    # -----------------------------------------------------------------------
    if dry_run:
        print(f"\n[DRY RUN] Source playlist: {source_playlist_name!r}")
        print(f"\nWould move {len(to_move)} track(s):")
        for t in to_move:
            print(f"  {t['artist']} — {t['title']}")
            destination_name = t.get("subgenre") or t.get("main_genre")
            print(f"    → {destination_name} (confidence {t['confidence']:.0%})")
            print(f"    → target playlist: {t['target_playlist_id']}")
        print(f"\nWould skip {sum(len(v) for v in skipped.values())} track(s):")
        for reason, labels in skipped.items():
            if labels:
                print(f"  {reason}: {len(labels)}")
        if remove_from_source:
            print(f"\nWould remove moved tracks from {source_playlist_name!r}.")
        else:
            print("\nSource playlist would NOT be modified (--no-remove).")
        return {
            "moved": len(to_move),
            "skipped": {k: len(v) for k, v in skipped.items()},
            "errors": [],
            "dry_run": True,
        }

    # -----------------------------------------------------------------------
    # Live run: add to target playlists, then remove from source
    # -----------------------------------------------------------------------
    sp = get_spotify_client(scope=_WRITE_SCOPE)

    errors: list[str] = []

    # Group tracks by target playlist
    by_target: dict[str, list[str]] = {}
    for t in to_move:
        tid = t["target_playlist_id"]
        by_target.setdefault(tid, []).append(t["spotify_track_id"])

    moved_track_ids: list[str] = []

    for target_playlist_id, track_ids in by_target.items():
        # Fetch existing track IDs in target to avoid duplicates
        try:
            existing_ids: set[str] = set()
            page = sp.playlist_items(
                target_playlist_id,
                fields="items(item(id)),next",
                additional_types=["track"],
                limit=_SPOTIFY_BATCH_SIZE,
            )
            while page:
                for item in page.get("items", []):
                    t = item.get("item")
                    if t and t.get("id"):
                        existing_ids.add(t["id"])
                page = sp.next(page) if page.get("next") else None

            new_ids = [tid for tid in track_ids if tid not in existing_ids]

            # Add in batches of 100
            for i in range(0, len(new_ids), _SPOTIFY_BATCH_SIZE):
                batch = new_ids[i : i + _SPOTIFY_BATCH_SIZE]
                sp._post(
                    f"playlists/{target_playlist_id}/items",
                    payload={
                        "uris": [f"spotify:track:{track_id}" for track_id in batch]
                    },
                )
                time.sleep(0.1)  # Spotify rate-limit safety

            # Tracks already present in the target are also safe to remove
            # from the source: the desired destination state is satisfied.
            moved_track_ids.extend(track_ids)
            print(
                f"  Added {len(new_ids)} track(s) to playlist {target_playlist_id}"
                + (f" ({len(track_ids) - len(new_ids)} already present)" if len(track_ids) != len(new_ids) else "")
            )

        except Exception as exc:  # noqa: BLE001
            errors.append(f"Error adding to {target_playlist_id}: {exc}")
            print(f"  [ERROR] {errors[-1]}")

    # Remove moved tracks from source playlist
    if remove_from_source and moved_track_ids:
        try:
            for i in range(0, len(moved_track_ids), _SPOTIFY_BATCH_SIZE):
                batch = moved_track_ids[i : i + _SPOTIFY_BATCH_SIZE]
                sp._delete(
                    f"playlists/{source_playlist_id}/items",
                    payload={
                        "items": [
                            {"uri": f"spotify:track:{track_id}"}
                            for track_id in batch
                        ]
                    },
                )
                time.sleep(0.1)
            print(f"  Removed {len(moved_track_ids)} track(s) from {source_playlist_name!r}.")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Error removing from source {source_playlist_id}: {exc}")
            print(f"  [ERROR] {errors[-1]}")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    summary = {
        "moved": len(moved_track_ids),
        "skipped": {k: len(v) for k, v in skipped.items()},
        "errors": errors,
        "dry_run": False,
    }

    print(f"\n{'=' * 50}")
    print(f"Moved:   {summary['moved']} tracks")
    print(f"Skipped: {sum(summary['skipped'].values())} tracks")
    for reason, count in summary["skipped"].items():
        if count:
            print(f"  → {reason}: {count}")
    if errors:
        print(f"Errors:  {len(errors)}")
        for err in errors:
            print(f"  ! {err}")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Web-researched Spotify playlist classifier and sorter.\n\n"
            "PART 1 (--prepare-research): fetch rich metadata; no classification API.\n"
            "PART 1B (--validate-research/--approve-research): validate or approve Codex research.\n"
            "PART 2 (--sort): sort approved tracks into subgenre playlists."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--prepare-research",
        "--classify",
        dest="prepare_research",
        action="store_true",
        help="PART 1 — fetch metadata and create provisional research artifacts.",
    )
    mode.add_argument(
        "--validate-research",
        action="store_true",
        help="PART 1B — validate a complete provisional artifact without mutations.",
    )
    mode.add_argument(
        "--approve-research",
        action="store_true",
        help="PART 1B — validate researched JSON and create classify_results.json.",
    )
    mode.add_argument(
        "--sort",
        action="store_true",
        help="PART 2 — sort classified tracks into subgenre playlists.",
    )

    # Part 1 args
    parser.add_argument(
        "--playlist",
        metavar="ID_or_NAME",
        help="[--prepare-research] Spotify playlist ID, URI, URL, or exact name.",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help=(
            "[--prepare-research] Provisional JSON path (derived from playlist by default). "
            f"[--approve-research] Part 2 JSON path (default: {DEFAULT_RESULTS_PATH})."
        ),
    )
    parser.add_argument(
        "--markdown-output",
        metavar="FILE",
        help="[--prepare-research] Markdown path (derived from playlist by default).",
    )
    parser.add_argument(
        "--schema",
        metavar="FILE",
        default=str(DEFAULT_SCHEMA_PATH),
        help=f"Schema path (default: {DEFAULT_SCHEMA_PATH}).",
    )

    # Part 1B / Part 2 args
    parser.add_argument(
        "--input",
        metavar="FILE",
        help=(
            "[--validate-research/--approve-research] Provisional research JSON (required). "
            f"[--sort] Approved JSON (default: {DEFAULT_RESULTS_PATH})."
        ),
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.5,
        metavar="FLOAT",
        help="[--sort] Minimum confidence to move a track (default: 0.5).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="[--sort] Preview moves without touching Spotify.",
    )
    parser.add_argument(
        "--no-remove",
        action="store_true",
        help="[--sort] Add to target playlists but do NOT remove from source.",
    )

    args = parser.parse_args()
    schema_path = Path(args.schema)

    if args.prepare_research:
        if not args.playlist:
            parser.error("--prepare-research requires --playlist <ID_or_NAME>")
        print(f"\nPart 1 — Preparing web research for: {args.playlist!r}")
        prepare_research(
            args.playlist,
            env=load_env(),
            schema=load_schema(schema_path),
            json_output_path=args.output,
            markdown_output_path=args.markdown_output,
        )

    elif args.validate_research:
        if not args.input:
            parser.error("--validate-research requires --input <research.json>")
        print(f"\nPart 1B — Validating researched classifications from: {args.input!r}")
        research = json.loads(Path(args.input).read_text(encoding="utf-8"))
        validate_research(research, load_schema(schema_path))
        print(
            f"Research validation passed: {research['total_tracks']} fully classified track(s)."
        )

    elif args.approve_research:
        if not args.input:
            parser.error("--approve-research requires --input <research.json>")
        print(f"\nPart 1B — Approving researched classifications from: {args.input!r}")
        approve_research(
            args.input,
            schema_path=schema_path,
            output_path=args.output or DEFAULT_RESULTS_PATH,
        )

    elif args.sort:
        input_path = args.input or str(DEFAULT_RESULTS_PATH)
        print(f"\nPart 2 — Sorting classified tracks from: {input_path!r}")
        if args.dry_run:
            print("(dry-run mode — no Spotify writes)\n")
        sort_tracks(
            input_path,
            env=load_env(),
            schema=load_schema(schema_path),
            min_confidence=args.min_confidence,
            dry_run=args.dry_run,
            remove_from_source=not args.no_remove,
        )


if __name__ == "__main__":
    main()
