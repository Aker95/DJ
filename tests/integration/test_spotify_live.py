"""
Integration tests against a static Spotify playlist owned by the project owner.

Playlist: "Test Playlist"  (public, never modified)
ID: 3FCiuZ8yhAu7iTf5k41Vio

Expected tracks (fixed forever):
  4HYWGaqCkshGWu6stZxlmd  Funk Tribu          — I Got It For You
  7aIb17DMLcOhLJIc9vF6Aa  Funk Tribu          — Phonky Tribu
  3OgdqZIgGIqusstV9IqX6p  KI/KI               — What's a Girl to Do in '25
  5jWqlILvwGMwsk3ZKfhxPU  Odymel              — Gucci
  5bcvu6T9QzdNFxTy6FZ0WQ  Odymel              — Thank You (Rework)

Run with:
    pytest tests/integration/ -m integration -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

pytestmark = pytest.mark.integration

TEST_PLAYLIST_ID = "3FCiuZ8yhAu7iTf5k41Vio"

EXPECTED_TRACKS = [
    {"id": "4HYWGaqCkshGWu6stZxlmd", "artist": "Funk Tribu",  "title": "I Got It For You"},
    {"id": "7aIb17DMLcOhLJIc9vF6Aa", "artist": "Funk Tribu",  "title": "Phonky Tribu"},
    {"id": "3OgdqZIgGIqusstV9IqX6p", "artist": "KI/KI",       "title": "What's a Girl to Do in '25"},
    {"id": "5jWqlILvwGMwsk3ZKfhxPU", "artist": "Odymel",      "title": "Gucci"},
    {"id": "5bcvu6T9QzdNFxTy6FZ0WQ", "artist": "Odymel",      "title": "Thank You (Rework)"},
]


@pytest.fixture(scope="module")
def sp():
    from utils.spotify_auth import get_spotify_client
    return get_spotify_client(scope="playlist-read-private playlist-read-collaborative")


@pytest.fixture(scope="module")
def live_tracks(sp):
    import importlib
    mod = importlib.import_module("01_spotify_snapshot")
    return mod.fetch_all_tracks(sp, TEST_PLAYLIST_ID)


class TestFetchAllTracksLive:
    def test_returns_all_five_tracks(self, live_tracks):
        assert len(live_tracks) == 5

    def test_track_ids_match(self, live_tracks):
        returned_ids = {t["id"] for t in live_tracks}
        expected_ids = {t["id"] for t in EXPECTED_TRACKS}
        assert returned_ids == expected_ids

    def test_track_order_is_preserved(self, live_tracks):
        assert live_tracks[0]["id"] == EXPECTED_TRACKS[0]["id"]
        assert live_tracks[4]["id"] == EXPECTED_TRACKS[4]["id"]

    def test_artist_names(self, live_tracks):
        by_id = {t["id"]: t for t in live_tracks}
        for exp in EXPECTED_TRACKS:
            assert by_id[exp["id"]]["artist"] == exp["artist"]

    def test_titles(self, live_tracks):
        by_id = {t["id"]: t for t in live_tracks}
        for exp in EXPECTED_TRACKS:
            assert by_id[exp["id"]]["title"] == exp["title"]

    def test_duration_ms_is_positive_int(self, live_tracks):
        for t in live_tracks:
            assert isinstance(t["duration_ms"], int)
            assert t["duration_ms"] > 0

    def test_no_null_ids(self, live_tracks):
        for t in live_tracks:
            assert t["id"] is not None


class TestFetchAllPlaylistsLive:
    def test_test_playlist_is_discoverable(self, sp):
        import importlib
        mod = importlib.import_module("01_spotify_snapshot")
        all_playlists = mod.fetch_all_playlists(sp)
        ids = [pl["id"] for pl in all_playlists]
        assert TEST_PLAYLIST_ID in ids

    def test_test_playlist_has_correct_name(self, sp):
        import importlib
        mod = importlib.import_module("01_spotify_snapshot")
        all_playlists = mod.fetch_all_playlists(sp)
        pl = next(p for p in all_playlists if p["id"] == TEST_PLAYLIST_ID)
        assert pl["name"] == "Test Playlist"
