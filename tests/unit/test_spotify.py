from __future__ import annotations

import copy

from tests.helpers import sample_schema, settings_for

from dj_library.spotify import (
    apply_schema_manifest,
    apply_sort_manifest,
    audit_sort,
    build_schema_manifest,
    build_snapshot,
    build_sort_manifest,
    resolve_playlist_id,
)


class SpotifyFake:
    def __init__(self) -> None:
        self.playlists = {
            "source": {"id": "source", "name": "Source"},
            "deep-house-playlist": {"id": "deep-house-playlist", "name": "old name"},
        }
        self.memberships = {
            "source": {"track"},
            "deep-house-playlist": set(),
        }
        self.posts = 0

    def current_user_playlists(self, limit=50):
        return {"items": list(self.playlists.values()), "next": None}

    def next(self, _page):
        return None

    def playlist(self, playlist_id, fields=None):
        return self.playlists[playlist_id]

    def playlist_items(self, playlist_id, **_kwargs):
        return {
            "items": [
                {
                    "item": {
                        "id": track_id,
                        "name": "Ocean",
                        "artists": [{"id": "artist", "name": "Example"}],
                        "album": {"id": "album", "name": "Release", "release_date": "2025"},
                        "duration_ms": 180000,
                        "external_ids": {"isrc": "XX"},
                        "external_urls": {"spotify": "https://example.test"},
                    }
                }
                for track_id in self.memberships.get(playlist_id, set())
            ],
            "next": None,
        }

    def _post(self, endpoint, payload):
        self.posts += 1
        if endpoint == "me/playlists":
            playlist_id = f"created-{self.posts}"
            playlist = {"id": playlist_id, "name": payload["name"]}
            self.playlists[playlist_id] = playlist
            self.memberships[playlist_id] = set()
            return playlist
        playlist_id = endpoint.split("/")[1]
        self.memberships.setdefault(playlist_id, set()).update(
            uri.rsplit(":", 1)[-1] for uri in payload["uris"]
        )

    def _delete(self, endpoint, payload):
        playlist_id = endpoint.split("/")[1]
        self.memberships[playlist_id].difference_update(
            item["uri"].rsplit(":", 1)[-1] for item in payload["items"]
        )

    def playlist_change_details(self, playlist_id, name):
        self.playlists[playlist_id]["name"] = name


def test_snapshot_is_read_only_and_carries_registry_metadata():
    fake = SpotifyFake()
    fake.playlists["deep-house-playlist"]["name"] = "01 • 118–125 WARM/GROOVE • House • Deep House"
    fake.memberships["deep-house-playlist"] = {"track"}
    before = copy.deepcopy(sample_schema())
    snapshot = build_snapshot(
        fake,
        before,
        registry={"tracks": {"track": {"metadata_bpm": 124}}},
    )
    assert snapshot["summary"]["tracks"] == 1
    assert snapshot["playlists"][0]["tracks"][0]["metadata_bpm"] == 124
    assert before == sample_schema()


def test_resolve_playlist_accepts_uri_url_and_unique_name():
    fake = SpotifyFake()
    playlist_id = "a" * 22
    assert resolve_playlist_id(fake, f"spotify:playlist:{playlist_id}") == playlist_id
    assert (
        resolve_playlist_id(fake, f"https://open.spotify.com/playlist/{playlist_id}") == playlist_id
    )
    assert resolve_playlist_id(fake, "Source") == "source"


def test_sort_plan_apply_is_idempotent_and_auditable():
    fake = SpotifyFake()
    results = {
        "source_playlist_id": "source",
        "tracks": [
            {
                "spotify_track_id": "track",
                "artist": "Example",
                "title": "Ocean",
                "approved": True,
                "main_genre": "House",
                "target_playlist_id": "deep-house-playlist",
            }
        ],
    }
    manifest = build_sort_manifest(results)
    first = apply_sort_manifest(manifest, spotify=fake)
    second = apply_sort_manifest(manifest, spotify=fake)
    assert first["summary"] == {"satisfied": 1, "added": 1}
    assert second["summary"] == {"satisfied": 1, "added": 0}
    assert audit_sort(results, spotify=fake)["ok"]


def test_schema_changes_only_during_apply(tmp_path):
    settings = settings_for(tmp_path)
    schema = sample_schema()
    schema["master_folders"][0]["genre_placements"][0]["spotify_playlist"] = None
    schema["master_folders"][1]["genre_placements"][0]["spotify_playlist"] = None
    schema["master_folders"][1]["genre_placements"][0]["subgenres"][0]["spotify_playlist"] = None
    fake = SpotifyFake()
    before = copy.deepcopy(schema)
    manifest = build_schema_manifest(schema, spotify=fake, settings=settings)
    assert schema == before
    result = apply_schema_manifest(manifest, spotify=fake, schema=schema, settings=settings)
    assert result["summary"]["provisioned"] == 3
    assert schema != before
