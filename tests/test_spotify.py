from spotify_playlist import apply_playlist_plan, build_playlist_plan
from spotify_snapshot import build_snapshot
from spotify_sort import apply_sort, build_sort_plan
from tests.helpers import sample_schema


class FakeSpotify:
    def __init__(self):
        self.playlists = {
            "source": {"id": "source", "name": "Research Bag", "tracks": {"track-1"}},
            "leaf-playlist": {"id": "leaf-playlist", "name": "FAST • Techno • Hard Groove", "tracks": set()},
        }

    def current_user_playlists(self, limit=50):
        return {"items": [{"id": item["id"], "name": item["name"]} for item in self.playlists.values()], "next": None}

    def playlist_items(self, playlist_id, **_kwargs):
        return {
            "items": [
                {
                    "item": {
                        "id": track_id,
                        "name": "Track",
                        "artists": [{"id": "artist", "name": "Artist"}],
                        "album": {"id": "album", "name": "Release", "release_date": "2026"},
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
        "tracks": [{"spotify_track_id": "track-1", "target_playlist_id": "leaf-playlist"}],
    }
    result = apply_sort(build_sort_plan(classifications), spotify)
    assert result["satisfied"] == 1
    assert spotify.playlists["leaf-playlist"]["tracks"] == {"track-1"}
    assert spotify.playlists["source"]["tracks"] == set()


def test_sort_does_not_remove_a_track_when_source_is_its_target():
    spotify = FakeSpotify()
    classifications = {
        "source_playlist_id": "source",
        "tracks": [{"spotify_track_id": "track-1", "target_playlist_id": "source"}],
    }
    apply_sort(build_sort_plan(classifications), spotify)
    assert spotify.playlists["source"]["tracks"] == {"track-1"}


def test_playlist_provisioning_has_one_responsibility():
    spotify = FakeSpotify()
    destination = {
        "placement_id": "fast:techno:new",
        "spotify_playlist_name": "FAST • Techno • New",
        "spotify_playlist": None,
        "node": {"spotify_playlist": None},
    }
    plan = build_playlist_plan(spotify, destination)
    result = apply_playlist_plan(spotify, destination, plan, public=False)
    assert result["playlist_id"] == "created"
    assert destination["node"]["spotify_playlist"] == "created"
