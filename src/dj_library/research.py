"""Spotify metadata preparation and human/agent-reviewed genre approval."""

from __future__ import annotations

import copy
from collections import Counter
from typing import Any

from .artifacts import now_iso, seal_manifest
from .schema import (
    bpm_master,
    canonical_key,
    iter_destinations,
    main_genre_index,
    master_folder_index,
    resolve_destination,
    slug,
    validate_schema,
)
from .spotify import fetch_playlist_tracks, resolve_playlist_id

REVIEW_FLAGS = {
    "artist-only",
    "broad-source",
    "conflicting-sources",
    "inferred-from-metadata",
    "new-taxonomy",
    "low-evidence",
    "inferred-bpm",
}


def _blank_track(track: dict) -> dict:
    return {
        **track,
        "main_genre": None,
        "subgenre": None,
        "classification_level": None,
        "master_folder": None,
        "metadata_bpm": None,
        "metadata_bpm_sources": [],
        "bpm_basis": None,
        "confidence": 0.0,
        "rationale": "",
        "evidence": [],
        "review_flags": [],
        "review_status": "pending",
        "target_playlist_id": None,
        "provisional_placement_id": None,
        "provisional_playlist_name": None,
        "provisional_local_folder": None,
        "already_in_target_playlist": False,
    }


def prepare_research(playlist: str, *, spotify: Any, schema: dict) -> dict:
    """Fetch exact Spotify metadata and return an unclassified review artifact."""

    playlist_id = resolve_playlist_id(spotify, playlist)
    metadata = spotify.playlist(playlist_id, fields="name")
    tracks = fetch_playlist_tracks(spotify, playlist_id, rich=True) or []
    destinations = [
        {
            key: destination.get(key)
            for key in (
                "placement_id",
                "master_folder",
                "bpm_min",
                "bpm_max",
                "bpm_max_inclusive",
                "main_genre",
                "subgenre",
                "classification_level",
                "spotify_playlist_name",
            )
        }
        for destination in iter_destinations(schema)
    ]
    return {
        "artifact_type": "spotify_genre_research",
        "artifact_version": 3,
        "status": "provisional",
        "prepared_at": now_iso(),
        "source_playlist_id": playlist_id,
        "source_playlist_name": metadata.get("name", playlist_id),
        "schema_version": schema["_meta"]["version"],
        "classification_policy": "codex_web_research_v3",
        "taxonomy_context": {
            "main_genres": [
                {"name": genre["name"], "aliases": genre.get("aliases", [])}
                for genre in schema.get("main_genres", [])
            ],
            "destinations": destinations,
        },
        "tracks": [_blank_track(track) for track in tracks],
        "proposed_main_genres": [],
        "proposed_subgenres": [],
        "proposed_aliases": [],
        "proposed_routing_changes": [],
        "summary": {"tracks": len(tracks), "pending": len(tracks)},
    }


def apply_decisions(research: dict, decisions: dict) -> dict:
    """Merge a compact agent/UI decision document into a provisional artifact."""

    updated = copy.deepcopy(research)
    by_id = {track["spotify_track_id"]: track for track in updated.get("tracks", [])}
    decision_by_id: dict[str, dict] = {}
    for group in decisions.get("groups", []):
        for track_id in group.get("track_ids", []):
            decision_by_id[track_id] = group.get("decision", {})
    for track_id, decision in decisions.get("track_overrides", {}).items():
        decision_by_id[track_id] = decision
    for decision in decisions.get("tracks", []):
        track_id = decision.get("spotify_track_id") or decision.get("spotify_id")
        if track_id:
            decision_by_id[track_id] = decision

    unknown = sorted(set(decision_by_id) - set(by_id))
    if unknown:
        raise ValueError(f"Decisions reference unknown Spotify tracks: {', '.join(unknown)}")
    for track_id, decision in decision_by_id.items():
        track = by_id[track_id]
        track.update(copy.deepcopy(decision))
        track["review_status"] = decision.get("review_status", "proposed")
        track["classification_level"] = "subgenre" if track.get("subgenre") else "main_genre"

    for field in (
        "proposed_main_genres",
        "proposed_subgenres",
        "proposed_aliases",
        "proposed_routing_changes",
    ):
        if field in decisions:
            updated[field] = copy.deepcopy(decisions[field])
    return refresh_research(updated)


def refresh_research(research: dict) -> dict:
    tracks = research.get("tracks", [])
    by_main = Counter(track.get("main_genre") for track in tracks if track.get("main_genre"))
    by_sub = Counter(track.get("subgenre") for track in tracks if track.get("subgenre"))
    research["summary"] = {
        "tracks": len(tracks),
        "pending": sum(not track.get("main_genre") for track in tracks),
        "parent_fallbacks": sum(
            bool(track.get("main_genre") and not track.get("subgenre")) for track in tracks
        ),
        "review_flagged": sum(bool(track.get("review_flags")) for track in tracks),
        "by_main_genre": dict(sorted(by_main.items())),
        "by_subgenre": dict(sorted(by_sub.items())),
        "proposed_main_genres": len(research.get("proposed_main_genres", [])),
        "proposed_subgenres": len(research.get("proposed_subgenres", [])),
    }
    return research


def _source_url(source: Any) -> str | None:
    if isinstance(source, str):
        return source
    return source.get("url") if isinstance(source, dict) else None


def validate_research(research: dict, schema: dict) -> dict:
    """Validate completeness and return a summary or raise one combined error."""

    validate_schema(schema)
    errors: list[str] = []
    seen: set[str] = set()
    known_genres = main_genre_index(schema)
    known_masters = master_folder_index(schema)
    proposed_genres = {
        canonical_key(item.get("name", "")) for item in research.get("proposed_main_genres", [])
    }
    proposed_subgenres = {
        (
            canonical_key(item.get("master_folder", "")),
            canonical_key(item.get("main_genre", "")),
            canonical_key(item.get("name", "")),
        )
        for item in research.get("proposed_subgenres", [])
    }
    for index, track in enumerate(research.get("tracks", []), 1):
        track_id = track.get("spotify_track_id")
        label = track_id or f"track {index}"
        if not track_id or track_id in seen:
            errors.append(f"{label}: Spotify ID must be present and unique")
        seen.add(track_id)
        main_genre = track.get("main_genre")
        master_name = track.get("master_folder")
        if not main_genre:
            errors.append(f"{label}: every valid track requires a main genre")
            continue
        if (
            canonical_key(main_genre) not in known_genres
            and canonical_key(main_genre) not in proposed_genres
        ):
            errors.append(f"{label}: unknown main genre {main_genre!r}")
        if not master_name or canonical_key(master_name) not in known_masters:
            errors.append(f"{label}: unknown BPM master {master_name!r}")
        confidence = track.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            errors.append(f"{label}: confidence must be between 0 and 1")
        if not str(track.get("rationale", "")).strip():
            errors.append(f"{label}: rationale is required")
        evidence = track.get("evidence") or []
        if not evidence or not all(_source_url(source) for source in evidence):
            errors.append(f"{label}: at least one evidence URL is required")
        flags = set(track.get("review_flags") or [])
        unknown_flags = flags - REVIEW_FLAGS
        if unknown_flags:
            errors.append(f"{label}: unsupported review flags {sorted(unknown_flags)!r}")
        if "artist-only" in flags and float(confidence or 0) > 0.8:
            errors.append(f"{label}: artist-only evidence cannot exceed 0.8 confidence")
        metadata_bpm = track.get("metadata_bpm")
        if metadata_bpm is not None and master_name in {
            item["name"] for item in schema["master_folders"]
        }:
            actual_master = bpm_master(schema, metadata_bpm)
            if not actual_master or canonical_key(actual_master["name"]) != canonical_key(
                master_name
            ):
                errors.append(f"{label}: metadata BPM does not match the selected master folder")
        if track.get("bpm_basis") == "inferred" and "inferred-bpm" not in flags:
            errors.append(f"{label}: inferred BPM requires the inferred-bpm flag")
        subgenre = track.get("subgenre")
        if subgenre:
            destination = resolve_destination(
                schema,
                master_folder=master_name,
                main_genre=main_genre,
                subgenre=subgenre,
            )
            proposed_key = (
                canonical_key(master_name),
                canonical_key(main_genre),
                canonical_key(subgenre),
            )
            if destination is None and proposed_key not in proposed_subgenres:
                errors.append(f"{label}: subgenre route is neither canonical nor proposed")
    if errors:
        raise ValueError("Research validation failed:\n- " + "\n- ".join(errors))
    return {
        "tracks": len(research.get("tracks", [])),
        "main_genres": len({track.get("main_genre") for track in research.get("tracks", [])}),
        "review_flagged": sum(
            bool(track.get("review_flags")) for track in research.get("tracks", [])
        ),
    }


def _find_placement(schema: dict, master_name: str, genre_id: str) -> dict | None:
    master = master_folder_index(schema).get(canonical_key(master_name))
    if not master:
        return None
    return next(
        (
            item
            for item in master.get("genre_placements", [])
            if item.get("main_genre_id") == genre_id
        ),
        None,
    )


def _apply_taxonomy_proposals(schema: dict, research: dict) -> dict:
    updated = copy.deepcopy(schema)
    for proposal in research.get("proposed_main_genres", []):
        if canonical_key(proposal["name"]) in main_genre_index(updated):
            continue
        updated["main_genres"].append(
            {
                "id": proposal.get("id") or slug(proposal["name"]),
                "name": proposal["name"],
                "aliases": proposal.get("aliases", []),
                "spotify_playlist": None,
                "notes": proposal.get("rationale") or proposal.get("notes"),
            }
        )
    for proposal in research.get("proposed_routing_changes", []):
        genre = main_genre_index(updated).get(canonical_key(proposal["main_genre"]))
        master = master_folder_index(updated).get(canonical_key(proposal["master_folder"]))
        if not genre or not master:
            raise ValueError(f"Invalid routing proposal: {proposal!r}")
        if _find_placement(updated, master["name"], genre["id"]) is None:
            master["genre_placements"].append(
                {
                    "main_genre_id": genre["id"],
                    "local_folder": (
                        f"{master['local_path']}/{slug(genre['name']).replace('-', ' ')}"
                    ),
                    "spotify_playlist": None,
                    "subgenres": [],
                }
            )
    for proposal in research.get("proposed_subgenres", []):
        genre = main_genre_index(updated).get(canonical_key(proposal["main_genre"]))
        if not genre:
            raise ValueError(f"Unknown proposal main genre {proposal['main_genre']!r}")
        placement = _find_placement(updated, proposal["master_folder"], genre["id"])
        if placement is None:
            raise ValueError(
                f"Subgenre proposal requires an existing main-genre placement: {proposal!r}"
            )
        labels = [item.get("name", "") for item in placement.get("subgenres", [])]
        if canonical_key(proposal["name"]) in {canonical_key(label) for label in labels}:
            continue
        placement.setdefault("subgenres", []).append(
            {
                "id": proposal.get("id") or slug(proposal["name"]),
                "name": proposal["name"],
                "aliases": proposal.get("aliases", []),
                "local_folder": (
                    f"{placement['local_folder']}/{slug(proposal['name']).replace('-', ' ')}"
                ),
                "spotify_playlist": None,
                "notes": proposal.get("rationale") or proposal.get("notes"),
            }
        )
    for proposal in research.get("proposed_aliases", []):
        canonical = proposal.get("canonical_name") or proposal.get("name")
        alias = proposal.get("alias")
        node = main_genre_index(updated).get(canonical_key(canonical or ""))
        if node and alias and alias not in node.setdefault("aliases", []):
            node["aliases"].append(alias)
    validate_schema(updated)
    return updated


def approve_research(
    research: dict, schema: dict, *, decisions: dict | None = None
) -> tuple[dict, dict, dict]:
    """Validate review decisions and return approved results, schema, and audit manifest."""

    reviewed = (
        apply_decisions(research, decisions)
        if decisions
        else refresh_research(copy.deepcopy(research))
    )
    validate_research(reviewed, schema)
    updated_schema = _apply_taxonomy_proposals(schema, reviewed)
    approved_tracks: list[dict] = []
    for track in reviewed.get("tracks", []):
        destination = resolve_destination(
            updated_schema,
            master_folder=track["master_folder"],
            main_genre=track["main_genre"],
            subgenre=track.get("subgenre"),
        )
        if destination is None:
            raise ValueError(
                f"Approved track has no canonical destination: {track['spotify_track_id']}"
            )
        approved_tracks.append(
            {
                **track,
                "approved": True,
                "review_status": "approved",
                "classification_level": destination["classification_level"],
                "placement_id": destination["placement_id"],
                "target_playlist_id": destination.get("spotify_playlist"),
                "target_playlist_name": destination["spotify_playlist_name"],
                "local_folder": destination["local_folder"],
            }
        )
    results = {
        "artifact_type": "spotify_classify_results",
        "artifact_version": 3,
        "classification_method": "codex_web_research_v3",
        "classified_at": now_iso(),
        "source_playlist_id": reviewed["source_playlist_id"],
        "source_playlist_name": reviewed.get("source_playlist_name"),
        "tracks": approved_tracks,
        "summary": {"approved": len(approved_tracks)},
    }
    audit = seal_manifest(
        {
            "artifact_type": "research_approval_manifest",
            "artifact_version": 1,
            "created_at": now_iso(),
            "source_playlist_id": reviewed["source_playlist_id"],
            "approved_track_ids": [track["spotify_track_id"] for track in approved_tracks],
            "schema_version": updated_schema["_meta"]["version"],
        }
    )
    return results, updated_schema, audit


def render_markdown(research: dict) -> str:
    """Render a compact human review without becoming a second data source."""

    lines = [
        f"# Genre Research: {research.get('source_playlist_name', 'Spotify playlist')}",
        "",
        f"- Status: **{research.get('status', 'provisional')}**",
        f"- Spotify playlist: `{research.get('source_playlist_id', '')}`",
        f"- Tracks: {len(research.get('tracks', []))}",
        "",
        "## Tracks",
        "",
    ]
    for index, track in enumerate(research.get("tracks", []), 1):
        lines.extend(
            [
                f"### {index}. {track.get('artist', '')} — {track.get('title', '')}",
                "",
                "- Route: "
                f"{track.get('master_folder') or 'pending'} / "
                f"{track.get('main_genre') or 'pending'} / "
                f"{track.get('subgenre') or '(parent)'}",
                f"- Confidence: {track.get('confidence', 0):.0%}",
                f"- Rationale: {track.get('rationale') or 'Pending research'}",
                f"- Flags: {', '.join(track.get('review_flags') or []) or 'none'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
