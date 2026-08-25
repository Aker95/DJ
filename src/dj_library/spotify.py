"""Spotify client boundaries, read-only snapshots, and playlist manifests."""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from .artifacts import now_iso, seal_manifest, sha256_file, verify_manifest
from .config import Settings
from .schema import canonical_key, iter_destinations, save_schema, validate_schema

READ_SCOPE = "playlist-read-private playlist-read-collaborative"
WRITE_SCOPE = f"{READ_SCOPE} playlist-modify-private playlist-modify-public"
SPOTIFY_BATCH_SIZE = 100


def get_client(
    settings: Settings,
    *,
    scope: str = READ_SCOPE,
    open_browser: bool = False,
) -> spotipy.Spotify:  # pragma: no cover
    """Create a cache-reusing client suitable for non-interactive agent tools."""

    settings.require_spotify()
    settings.spotify_cache_path.parent.mkdir(parents=True, exist_ok=True)
    auth = SpotifyOAuth(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        redirect_uri=settings.spotify_redirect_uri,
        scope=scope,
        cache_path=str(settings.spotify_cache_path),
        open_browser=open_browser,
    )
    return spotipy.Spotify(auth_manager=auth)


def authenticate(settings: Settings) -> dict:  # pragma: no cover
    """Perform the only browser-enabled authentication bootstrap action."""

    client = get_client(settings, scope=WRITE_SCOPE, open_browser=True)
    profile = client.current_user()
    return {"spotify_user_id": profile.get("id"), "display_name": profile.get("display_name")}


def fetch_all_playlists(spotify: Any) -> list[dict]:
    playlists: list[dict] = []
    page = spotify.current_user_playlists(limit=50)
    while page:
        playlists.extend(page.get("items", []))
        page = spotify.next(page) if page.get("next") else None
    return playlists


def fetch_playlist_tracks(
    spotify: Any, playlist_id: str, *, rich: bool = False
) -> list[dict] | None:
    """Fetch every available Spotify track; return None for inaccessible playlists."""

    fields = (
        "items(item(id,name,artists(id,name),album(id,name,release_date),duration_ms,"
        "external_ids(isrc),external_urls(spotify))),next"
        if rich
        else "items(item(id,name,artists(id,name),duration_ms)),next"
    )
    try:
        page = spotify.playlist_items(
            playlist_id,
            fields=fields,
            additional_types=["track"],
            limit=SPOTIFY_BATCH_SIZE,
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
            artists = [
                {"spotify_artist_id": artist.get("id"), "name": artist.get("name", "")}
                for artist in track.get("artists", [])
            ]
            record = {
                "spotify_track_id": track["id"],
                "id": track["id"],
                "artists": artists,
                "artist": ", ".join(value["name"] for value in artists if value["name"]),
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


def resolve_playlist_id(spotify: Any, value: str) -> str:
    candidate = value.strip()
    if candidate.startswith("spotify:playlist:"):
        candidate = candidate.rsplit(":", 1)[-1]
    elif "open.spotify.com" in candidate:
        parts = [part for part in urlparse(candidate).path.split("/") if part]
        if len(parts) >= 2 and parts[-2] == "playlist":
            candidate = parts[-1]
    if len(candidate) == 22 and candidate.isalnum():
        return candidate
    normalized = " ".join(value.split()).casefold()
    matches = [
        playlist["id"]
        for playlist in fetch_all_playlists(spotify)
        if " ".join((playlist.get("name") or "").split()).casefold() == normalized
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one Spotify playlist matching {value!r}; found {len(matches)}")
    return matches[0]


def _destination_indexes(schema: dict) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    by_id: dict[str, list[dict]] = defaultdict(list)
    by_name: dict[str, list[dict]] = defaultdict(list)
    for destination in iter_destinations(schema):
        if destination.get("spotify_playlist"):
            by_id[destination["spotify_playlist"]].append(destination)
        for label in (destination["name"], destination.get("spotify_playlist_name")):
            if label:
                by_name[canonical_key(label)].append(destination)
    return by_id, by_name


def _playlist_destination(playlist: dict, indexes: tuple[dict, dict]) -> dict | None:
    by_id, by_name = indexes
    name_key = canonical_key(playlist.get("name") or "")
    id_matches = by_id.get(playlist["id"], [])
    if len(id_matches) == 1:
        return id_matches[0]
    if len(id_matches) > 1:
        explicit = [
            item
            for item in id_matches
            if canonical_key(item.get("spotify_playlist_name", "")) == name_key
        ]
        return explicit[0] if len(explicit) == 1 else None
    name_matches = by_name.get(name_key, [])
    return name_matches[0] if len(name_matches) == 1 else None


def build_snapshot(
    spotify: Any,
    schema: dict,
    *,
    registry: dict | None = None,
) -> dict:
    """Build a read-only snapshot without backfilling or changing the schema."""

    prior_tracks = (registry or {}).get("tracks", {})
    indexes = _destination_indexes(schema)
    matched: list[dict] = []
    skipped_bags: list[str] = []
    inaccessible: list[str] = []
    for playlist in fetch_all_playlists(spotify):
        name = (playlist.get("name") or "").strip()
        if name.casefold().endswith("bag"):
            skipped_bags.append(name)
            continue
        destination = _playlist_destination(playlist, indexes)
        if destination is None:
            continue
        tracks = fetch_playlist_tracks(spotify, playlist["id"], rich=True)
        if tracks is None:
            inaccessible.append(playlist["id"])
            continue
        for track in tracks:
            prior = prior_tracks.get(track["spotify_track_id"], {})
            for key in (
                "metadata_bpm",
                "metadata_bpm_sources",
                "bpm_basis",
                "placement_id",
                "main_genre",
                "subgenre",
                "local_folder",
            ):
                if prior.get(key) is not None:
                    track[key] = prior[key]
        matched.append(
            {
                "playlist_id": playlist["id"],
                "playlist_name": name,
                "expected_playlist_name": destination["spotify_playlist_name"],
                "placement_id": destination["placement_id"],
                "classification_level": destination["classification_level"],
                "main_genre": destination["main_genre"],
                "subgenre": destination["subgenre"],
                "master_folder": destination["master_folder"],
                "bpm_min": destination["bpm_min"],
                "bpm_max": destination["bpm_max"],
                "bpm_max_inclusive": destination["bpm_max_inclusive"],
                "local_folder": destination["local_folder"],
                "tracks": tracks,
            }
        )
    return {
        "artifact_type": "spotify_snapshot",
        "artifact_version": 2,
        "generated_at": now_iso(),
        "playlists": matched,
        "summary": {
            "playlists": len(matched),
            "tracks": sum(len(item["tracks"]) for item in matched),
            "skipped_bag_playlists": len(skipped_bags),
            "inaccessible_playlists": inaccessible,
        },
    }


def playlist_track_ids(spotify: Any, playlist_id: str) -> set[str]:
    tracks = fetch_playlist_tracks(spotify, playlist_id) or []
    return {track["spotify_track_id"] for track in tracks}


def build_sort_manifest(results: dict, *, remove_from_source: bool = True) -> dict:
    """Build the only input accepted by live Spotify sorting."""

    skipped: dict[str, list[str]] = defaultdict(list)
    actions: list[dict] = []
    for track in results.get("tracks", []):
        label = f"{track.get('artist', '')} — {track.get('title', '')}"
        if track.get("approved") is not True:
            skipped["not_approved"].append(label)
        elif not track.get("main_genre"):
            skipped["unclassified"].append(label)
        elif not track.get("target_playlist_id"):
            skipped["no_target_playlist"].append(label)
        else:
            actions.append(
                {
                    "spotify_track_id": track["spotify_track_id"],
                    "target_playlist_id": track["target_playlist_id"],
                    "artist": track.get("artist"),
                    "title": track.get("title"),
                }
            )
    return seal_manifest(
        {
            "artifact_type": "spotify_sort_manifest",
            "artifact_version": 1,
            "status": "ready_for_review",
            "created_at": now_iso(),
            "source_playlist_id": results["source_playlist_id"],
            "remove_from_source": remove_from_source,
            "tracks": actions,
            "skipped": dict(skipped),
            "summary": {
                "tracks_to_sort": len(actions),
                "tracks_skipped": sum(len(items) for items in skipped.values()),
            },
        }
    )


def apply_sort_manifest(manifest: dict, *, spotify: Any) -> dict:
    verify_manifest(manifest, artifact_type="spotify_sort_manifest")
    if manifest.get("status") != "ready_for_review":
        raise ValueError("Spotify sort manifest is not ready to apply")
    by_target: dict[str, list[str]] = defaultdict(list)
    for track in manifest.get("tracks", []):
        by_target[track["target_playlist_id"]].append(track["spotify_track_id"])

    completed: list[str] = []
    added = 0
    for playlist_id, track_ids in by_target.items():
        existing = playlist_track_ids(spotify, playlist_id)
        new_ids = [track_id for track_id in track_ids if track_id not in existing]
        for offset in range(0, len(new_ids), SPOTIFY_BATCH_SIZE):
            batch = new_ids[offset : offset + SPOTIFY_BATCH_SIZE]
            spotify._post(
                f"playlists/{playlist_id}/items",
                payload={"uris": [f"spotify:track:{track_id}" for track_id in batch]},
            )
        added += len(new_ids)
        completed.extend(track_ids)

    if manifest.get("remove_from_source") and completed:
        source = manifest["source_playlist_id"]
        for offset in range(0, len(completed), SPOTIFY_BATCH_SIZE):
            batch = completed[offset : offset + SPOTIFY_BATCH_SIZE]
            spotify._delete(
                f"playlists/{source}/items",
                payload={"items": [{"uri": f"spotify:track:{track_id}"} for track_id in batch]},
            )
    return {
        "artifact_type": "spotify_sort_apply_result",
        "artifact_version": 1,
        "applied_at": now_iso(),
        "manifest_sha256": manifest["manifest_sha256"],
        "summary": {"satisfied": len(completed), "added": added},
    }


def audit_sort(results: dict, *, spotify: Any) -> dict:
    expected: dict[str, set[str]] = defaultdict(set)
    all_ids: set[str] = set()
    for track in results.get("tracks", []):
        if track.get("approved") is True and track.get("target_playlist_id"):
            expected[track["target_playlist_id"]].add(track["spotify_track_id"])
            all_ids.add(track["spotify_track_id"])
    missing = {
        playlist_id: sorted(track_ids - playlist_track_ids(spotify, playlist_id))
        for playlist_id, track_ids in expected.items()
    }
    missing = {playlist_id: ids for playlist_id, ids in missing.items() if ids}
    remaining = sorted(all_ids & playlist_track_ids(spotify, results["source_playlist_id"]))
    return {
        "ok": not missing and not remaining,
        "missing_from_targets": missing,
        "remaining_in_source": remaining,
        "summary": {"expected_tracks": len(all_ids), "target_playlists": len(expected)},
    }


def build_schema_manifest(schema: dict, *, spotify: Any, settings: Settings) -> dict:
    """Plan playlist renames and missing placement playlists without writes."""

    by_playlist: dict[str, list[dict]] = defaultdict(list)
    provisioning: list[dict] = []
    for destination in iter_destinations(schema):
        context = {
            "placement_id": destination["placement_id"],
            "expected_name": destination["spotify_playlist_name"],
            "master_folder": destination["master_folder"],
            "main_genre": destination["main_genre"],
            "subgenre": destination["subgenre"],
        }
        if destination.get("spotify_playlist"):
            by_playlist[destination["spotify_playlist"]].append(context)
        else:
            provisioning.append(context)
    renames: list[dict] = []
    conflicts: list[dict] = []
    for playlist_id, contexts in by_playlist.items():
        names = {item["expected_name"] for item in contexts}
        if len(names) != 1:
            conflicts.append({"playlist_id": playlist_id, "placements": contexts})
            continue
        metadata = spotify.playlist(playlist_id, fields="id,name")
        expected = next(iter(names))
        if metadata.get("name") != expected:
            renames.append(
                {
                    "playlist_id": playlist_id,
                    "current_name": metadata.get("name"),
                    "expected_name": expected,
                }
            )
    return seal_manifest(
        {
            "artifact_type": "spotify_schema_manifest",
            "artifact_version": 2,
            "status": "blocked" if conflicts else "ready_for_review",
            "created_at": now_iso(),
            "schema_sha256": sha256_file(settings.schema_path),
            "renames": renames,
            "provisioning": provisioning,
            "conflicts": conflicts,
            "summary": {
                "renames": len(renames),
                "provisioning": len(provisioning),
                "conflicts": len(conflicts),
            },
        }
    )


def apply_schema_manifest(
    manifest: dict,
    *,
    spotify: Any,
    schema: dict,
    settings: Settings,
    public: bool = False,
) -> dict:
    """Apply reviewed renames/provisioning and persist stable playlist IDs."""

    verify_manifest(manifest, artifact_type="spotify_schema_manifest")
    if manifest.get("status") != "ready_for_review" or manifest.get("conflicts"):
        raise ValueError("Spotify schema manifest is blocked")
    if sha256_file(settings.schema_path) != manifest["schema_sha256"]:
        raise ValueError("Schema changed after the Spotify schema plan was reviewed")

    for rename in manifest.get("renames", []):
        spotify.playlist_change_details(rename["playlist_id"], name=rename["expected_name"])

    account_playlists = fetch_all_playlists(spotify)
    by_name: dict[str, list[dict]] = defaultdict(list)
    for playlist in account_playlists:
        by_name[canonical_key(playlist.get("name") or "")].append(playlist)
    destinations = {item["placement_id"]: item for item in iter_destinations(schema)}
    provisioned: list[dict] = []
    for request in manifest.get("provisioning", []):
        matches = by_name.get(canonical_key(request["expected_name"]), [])
        if len(matches) > 1:
            raise ValueError(f"Multiple Spotify playlists named {request['expected_name']!r}")
        created = not matches
        playlist = (
            matches[0]
            if matches
            else spotify._post(
                "me/playlists",
                payload={
                    "name": request["expected_name"],
                    "description": "DJ library canonical placement.",
                    "public": public,
                },
            )
        )
        destination = destinations[request["placement_id"]]
        destination["node"]["spotify_playlist"] = playlist["id"]
        provisioned.append(
            {
                "placement_id": request["placement_id"],
                "playlist_id": playlist["id"],
                "created": created,
            }
        )
    validate_schema(schema)
    save_schema(schema, settings.schema_path)
    return {
        "artifact_type": "spotify_schema_apply_result",
        "artifact_version": 2,
        "applied_at": now_iso(),
        "manifest_sha256": manifest["manifest_sha256"],
        "renames": manifest.get("renames", []),
        "provisioned": provisioned,
        "summary": {"renamed": len(manifest.get("renames", [])), "provisioned": len(provisioned)},
    }
