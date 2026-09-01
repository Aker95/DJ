from spotify import (
    apply_provision_plan,
    apply_sort_plan,
    build_provision_plan,
    build_snapshot,
    build_sort_plan,
)

from tests.helpers import sample_schema


class FakeSpotify:
    def __init__(self):
        self.playlists = {
            "source": {"id": "source", "name": "Research Bag", "tracks": {"track-1"}},
            "leaf-playlist": {
                "id": "leaf-playlist",
                "name": "FAST • Techno • Hard Groove",
                "tracks": set(),
            },
        }

    def current_user_playlists(self, limit=50):
        return {
            "items": [
                {"id": item["id"], "name": item["name"]}
                for item in self.playlists.values()
            ],
            "next": None,
        }

    def playlist_items(self, playlist_id, **_kwargs):
        return {
            "items": [
                {
                    "item": {
                        "id": track_id,
                        "name": "Track",
                        "artists": [{"id": "artist", "name": "Artist"}],
                        "album": {
                            "id": "album",
                            "name": "Release",
                            "release_date": "2026",
                        },
                        "duration_ms": 300000,
                        "external_ids": {"isrc": "CODE"},
                        "external_urls": {"spotify": "https://spotify/track"},
                    }
                }
                for track_id in self.playlists[playlist_id]["tracks"]
            ],
            "next": None,
        }

    def next(self, _page):
        return None

    def current_user(self):
        return {"id": "user"}

    def user_playlist_create(self, _user, name, public=False, description=""):
        playlist_id = "created"
        self.playlists[playlist_id] = {"id": playlist_id, "name": name, "tracks": set()}
        return {"id": playlist_id, "name": name}

    def playlist_add_items(self, playlist_id, track_ids):
        self.playlists[playlist_id]["tracks"].update(track_ids)

    def playlist_remove_all_occurrences_of_items(self, playlist_id, track_ids):
        self.playlists[playlist_id]["tracks"].difference_update(track_ids)


def test_snapshot_maps_schema_playlist_and_skips_bag():
    spotify = FakeSpotify()
    spotify.playlists["source"]["name"] = "Staging Bag"
    spotify.playlists["leaf-playlist"]["tracks"] = {"track-1"}
    snapshot = build_snapshot(spotify, sample_schema())
    assert snapshot["summary"]["tracks"] == 1
    assert snapshot["summary"]["skipped_bags"] == ["Staging Bag"]


def test_sort_adds_target_then_removes_source():
    spotify = FakeSpotify()
    classifications = {
        "source_playlist_id": "source",
        "tracks": [
            {"spotify_track_id": "track-1", "target_playlist_id": "leaf-playlist"}
        ],
    }
    result = apply_sort_plan(spotify, build_sort_plan(classifications))
    assert result["satisfied"] == 1
    assert spotify.playlists["leaf-playlist"]["tracks"] == {"track-1"}
    assert spotify.playlists["source"]["tracks"] == set()


def test_sort_does_not_remove_a_track_when_source_is_its_target():
    spotify = FakeSpotify()
    classifications = {
        "source_playlist_id": "source",
        "tracks": [{"spotify_track_id": "track-1", "target_playlist_id": "source"}],
    }
    apply_sort_plan(spotify, build_sort_plan(classifications))
    assert spotify.playlists["source"]["tracks"] == {"track-1"}


def test_playlist_provisioning_has_one_responsibility():
    spotify = FakeSpotify()
    catalog = sample_schema()
    catalog["master_folders"][0]["genre_placements"][0]["subgenres"][0][
        "spotify_playlist"
    ] = None
    destination = {
        "placement_id": "fast:techno:hard-groove",
        "spotify_playlist_name": "FAST • Techno • Hard Groove",
        "spotify_playlist": None,
    }
    plan = build_provision_plan(spotify, destination)
    updated, result = apply_provision_plan(spotify, catalog, plan)
    assert result["playlist_id"] == "leaf-playlist"
    assert (
        updated["master_folders"][0]["genre_placements"][0]["subgenres"][0][
            "spotify_playlist"
        ]
        == "leaf-playlist"
    )
