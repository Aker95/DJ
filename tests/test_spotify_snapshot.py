"""
Tests for scripts/01_spotify_snapshot.py
"""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import importlib
snapshot_mod = importlib.import_module("01_spotify_snapshot")

fetch_all_playlists = snapshot_mod.fetch_all_playlists
fetch_all_tracks = snapshot_mod.fetch_all_tracks
build_subgenre_index = snapshot_mod.build_subgenre_index


# ---------------------------------------------------------------------------
# build_subgenre_index
# ---------------------------------------------------------------------------

class TestBuildSubgenreIndex:
    def test_all_subgenres_indexed(self, sample_schema):
        index = build_subgenre_index(sample_schema)
        assert "afro house" in index
        assert "deep house" in index
        assert "melodic house" in index
        assert "tech house" in index
        assert "bass house" in index

    def test_key_is_lowercase(self, sample_schema):
        index = build_subgenre_index(sample_schema)
        for key in index:
            assert key == key.lower()

    def test_value_contains_master_folder_name(self, sample_schema):
        index = build_subgenre_index(sample_schema)
        master_name, sg = index["afro house"]
        assert master_name == "DJ CHILL / GROOVE"

    def test_value_contains_subgenre_dict(self, sample_schema):
        index = build_subgenre_index(sample_schema)
        _, sg = index["tech house"]
        assert sg["name"] == "Tech House"
        assert sg["local_folder"] == "music/tech house"

    def test_empty_schema_returns_empty_index(self):
        schema = {"master_folders": []}
        assert build_subgenre_index(schema) == {}

    def test_subgenre_with_null_local_folder(self, sample_schema):
        index = build_subgenre_index(sample_schema)
        _, sg = index["melodic house"]
        assert sg["local_folder"] is None


# ---------------------------------------------------------------------------
# fetch_all_playlists — pagination
# ---------------------------------------------------------------------------

class TestFetchAllPlaylists:
    def test_single_page(self):
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [{"id": "p1", "name": "A"}, {"id": "p2", "name": "B"}],
            "next": None,
        }
        result = fetch_all_playlists(sp)
        assert len(result) == 2
        assert result[0]["id"] == "p1"

    def test_multiple_pages(self):
        page1 = {"items": [{"id": "p1", "name": "A"}], "next": "url2"}
        page2 = {"items": [{"id": "p2", "name": "B"}, {"id": "p3", "name": "C"}], "next": None}
        sp = MagicMock()
        sp.current_user_playlists.return_value = page1
        sp.next.side_effect = [page2, None]
        result = fetch_all_playlists(sp)
        assert len(result) == 3
        assert result[2]["id"] == "p3"

    def test_empty_library(self):
        sp = MagicMock()
        sp.current_user_playlists.return_value = {"items": [], "next": None}
        result = fetch_all_playlists(sp)
        assert result == []

    def test_calls_current_user_playlists_with_limit_50(self):
        sp = MagicMock()
        sp.current_user_playlists.return_value = {"items": [], "next": None}
        fetch_all_playlists(sp)
        sp.current_user_playlists.assert_called_once_with(limit=50)


# ---------------------------------------------------------------------------
# fetch_all_tracks — pagination and null filtering
# ---------------------------------------------------------------------------

class TestFetchAllTracks:
    def _make_track_item(self, tid, name, artist, duration=300000):
        return {
            "item": {
                "id": tid,
                "name": name,
                "artists": [{"name": artist}],
                "duration_ms": duration,
            }
        }

    def test_fetches_basic_tracks(self):
        sp = MagicMock()
        sp.playlist_items.return_value = {
            "items": [
                self._make_track_item("t1", "Song A", "Artist X"),
                self._make_track_item("t2", "Song B", "Artist Y"),
            ],
            "next": None,
        }
        result = fetch_all_tracks(sp, "pl_123")
        assert len(result) == 2
        assert result[0] == {"id": "t1", "title": "Song A", "artist": "Artist X", "duration_ms": 300000}

    def test_skips_null_tracks(self):
        sp = MagicMock()
        sp.playlist_items.return_value = {
            "items": [
                {"item": None},
                self._make_track_item("t1", "Song A", "Artist X"),
                {"item": {"id": None, "name": "Local file", "artists": [], "duration_ms": 0}},
            ],
            "next": None,
        }
        result = fetch_all_tracks(sp, "pl_123")
        assert len(result) == 1
        assert result[0]["id"] == "t1"

    def test_pagination_fetches_all_pages(self):
        sp = MagicMock()
        page1 = {
            "items": [self._make_track_item("t1", "Song A", "Artist X")],
            "next": "url2",
        }
        page2 = {
            "items": [self._make_track_item("t2", "Song B", "Artist Y")],
            "next": None,
        }
        sp.playlist_items.return_value = page1
        sp.next.side_effect = [page2]
        result = fetch_all_tracks(sp, "pl_123")
        assert len(result) == 2

    def test_uses_first_artist_only(self):
        sp = MagicMock()
        sp.playlist_items.return_value = {
            "items": [{
                "item": {
                    "id": "t1",
                    "name": "Collab Track",
                    "artists": [{"name": "Primary"}, {"name": "Feature"}],
                    "duration_ms": 200000,
                }
            }],
            "next": None,
        }
        result = fetch_all_tracks(sp, "pl_123")
        assert result[0]["artist"] == "Primary"

    def test_empty_playlist(self):
        sp = MagicMock()
        sp.playlist_items.return_value = {"items": [], "next": None}
        result = fetch_all_tracks(sp, "pl_empty")
        assert result == []


# ---------------------------------------------------------------------------
# main() — integration-level with mocks
# ---------------------------------------------------------------------------

class TestSnapshotMain:
    def _make_sp(self, playlists, tracks_by_id=None):
        sp = MagicMock()
        sp.current_user_playlists.return_value = {"items": playlists, "next": None}
        tracks_by_id = tracks_by_id or {}

        def playlist_items(pl_id, **kwargs):
            tracks = tracks_by_id.get(pl_id, [])
            return {
                "items": [
                    {
                        "item": {
                            "id": t["id"],
                            "name": t["title"],
                            "artists": [{"name": t["artist"]}],
                            "duration_ms": t.get("duration_ms", 300000),
                        }
                    }
                    for t in tracks
                ],
                "next": None,
            }

        sp.playlist_items.side_effect = playlist_items
        sp.next.return_value = None
        return sp

    def test_skips_bag_playlists(self, tmp_path, sample_schema):
        playlists = [
            {"id": "p_afro", "name": "Afro House"},
            {"id": "p_bag", "name": "Tech House Bag"},
            {"id": "p_mybag", "name": "myBag"},
        ]
        sp = self._make_sp(playlists, {"p_afro": [{"id": "t1", "title": "T", "artist": "A"}]})

        schema_path = tmp_path / "schema.json"
        schema_path.write_text(json.dumps(sample_schema, indent=2))
        snapshot_path = tmp_path / "spotify_snapshot.json"

        with (
            patch.object(snapshot_mod, "PROJECT_ROOT", tmp_path),
            patch.object(snapshot_mod, "SNAPSHOT_PATH", snapshot_path),
            patch.object(snapshot_mod, "get_spotify_client", return_value=sp),
            patch.object(snapshot_mod, "load_schema", return_value=sample_schema),
            patch.object(snapshot_mod, "save_schema"),
        ):
            snapshot_mod.main()

        snapshot = json.loads(snapshot_path.read_text())
        playlist_names = [p["playlist_name"] for p in snapshot["playlists"]]
        assert "Tech House Bag" not in playlist_names
        assert "myBag" not in playlist_names

    def test_bag_check_is_case_insensitive(self, tmp_path, sample_schema):
        playlists = [
            {"id": "p_bag1", "name": "Tech House BAG"},
            {"id": "p_bag2", "name": "deep house bag"},
        ]
        sp = self._make_sp(playlists)
        snapshot_path = tmp_path / "spotify_snapshot.json"

        with (
            patch.object(snapshot_mod, "PROJECT_ROOT", tmp_path),
            patch.object(snapshot_mod, "SNAPSHOT_PATH", snapshot_path),
            patch.object(snapshot_mod, "get_spotify_client", return_value=sp),
            patch.object(snapshot_mod, "load_schema", return_value=sample_schema),
            patch.object(snapshot_mod, "save_schema"),
        ):
            snapshot_mod.main()

        snapshot = json.loads(snapshot_path.read_text())
        assert snapshot["total_playlists"] == 0

    def test_matches_schema_subgenres_case_insensitively(self, tmp_path, sample_schema):
        playlists = [
            {"id": "p1", "name": "AFRO HOUSE"},
            {"id": "p2", "name": "tech house"},
        ]
        tracks = {"p1": [{"id": "t1", "title": "Track", "artist": "DJ"}]}
        sp = self._make_sp(playlists, tracks)
        snapshot_path = tmp_path / "spotify_snapshot.json"

        with (
            patch.object(snapshot_mod, "PROJECT_ROOT", tmp_path),
            patch.object(snapshot_mod, "SNAPSHOT_PATH", snapshot_path),
            patch.object(snapshot_mod, "get_spotify_client", return_value=sp),
            patch.object(snapshot_mod, "load_schema", return_value=sample_schema),
            patch.object(snapshot_mod, "save_schema"),
        ):
            snapshot_mod.main()

        snapshot = json.loads(snapshot_path.read_text())
        assert snapshot["total_playlists"] == 2

    def test_backfills_null_playlist_ids(self, tmp_path, sample_schema):
        playlists = [{"id": "new_pl_id", "name": "Afro House"}]
        tracks = {"new_pl_id": [{"id": "t1", "title": "T", "artist": "A"}]}
        sp = self._make_sp(playlists, tracks)
        snapshot_path = tmp_path / "spotify_snapshot.json"
        saved_schema = [None]

        def capture_save(s, path=None):
            saved_schema[0] = s

        with (
            patch.object(snapshot_mod, "PROJECT_ROOT", tmp_path),
            patch.object(snapshot_mod, "SNAPSHOT_PATH", snapshot_path),
            patch.object(snapshot_mod, "get_spotify_client", return_value=sp),
            patch.object(snapshot_mod, "load_schema", return_value=sample_schema),
            patch.object(snapshot_mod, "save_schema", side_effect=capture_save),
        ):
            snapshot_mod.main()

        assert saved_schema[0] is not None
        sg = saved_schema[0]["master_folders"][0]["subgenres"][0]
        assert sg["spotify_playlist"] == "new_pl_id"

    def test_does_not_overwrite_existing_playlist_id(self, tmp_path, sample_schema):
        playlists = [{"id": "different_id", "name": "Deep House"}]
        tracks = {"different_id": [{"id": "t1", "title": "T", "artist": "A"}]}
        sp = self._make_sp(playlists, tracks)
        snapshot_path = tmp_path / "spotify_snapshot.json"
        saved_schemas = []

        with (
            patch.object(snapshot_mod, "PROJECT_ROOT", tmp_path),
            patch.object(snapshot_mod, "SNAPSHOT_PATH", snapshot_path),
            patch.object(snapshot_mod, "get_spotify_client", return_value=sp),
            patch.object(snapshot_mod, "load_schema", return_value=sample_schema),
            patch.object(snapshot_mod, "save_schema", side_effect=lambda s, path=None: saved_schemas.append(s)),
        ):
            snapshot_mod.main()

        # Deep House already has "existing_pl_id" — should not be overwritten
        if saved_schemas:
            sg = saved_schemas[0]["master_folders"][0]["subgenres"][1]
            assert sg["spotify_playlist"] == "existing_pl_id"

    def test_snapshot_output_structure(self, tmp_path, sample_schema):
        playlists = [{"id": "p_afro", "name": "Afro House"}]
        tracks = {
            "p_afro": [
                {"id": "t1", "title": "Kawa", "artist": "Black Coffee", "duration_ms": 360000}
            ]
        }
        sp = self._make_sp(playlists, tracks)
        snapshot_path = tmp_path / "spotify_snapshot.json"

        with (
            patch.object(snapshot_mod, "PROJECT_ROOT", tmp_path),
            patch.object(snapshot_mod, "SNAPSHOT_PATH", snapshot_path),
            patch.object(snapshot_mod, "get_spotify_client", return_value=sp),
            patch.object(snapshot_mod, "load_schema", return_value=sample_schema),
            patch.object(snapshot_mod, "save_schema"),
        ):
            snapshot_mod.main()

        snap = json.loads(snapshot_path.read_text())
        assert "generated_at" in snap
        assert "total_playlists" in snap
        assert "playlists" in snap
        pl = snap["playlists"][0]
        assert pl["playlist_id"] == "p_afro"
        assert pl["schema_subgenre"] == "Afro House"
        assert pl["master_folder"] == "DJ CHILL / GROOVE"
        assert pl["local_folder"] == "music/afro house"
        assert len(pl["tracks"]) == 1
        assert pl["tracks"][0] == {
            "id": "t1", "title": "Kawa", "artist": "Black Coffee", "duration_ms": 360000
        }

    def test_ignores_unrecognised_playlists(self, tmp_path, sample_schema):
        playlists = [
            {"id": "p1", "name": "Afro House"},
            {"id": "p_unknown", "name": "My Random Playlist"},
            {"id": "p_unknown2", "name": "Favourites"},
        ]
        tracks = {"p1": [{"id": "t1", "title": "T", "artist": "A"}]}
        sp = self._make_sp(playlists, tracks)
        snapshot_path = tmp_path / "spotify_snapshot.json"

        with (
            patch.object(snapshot_mod, "PROJECT_ROOT", tmp_path),
            patch.object(snapshot_mod, "SNAPSHOT_PATH", snapshot_path),
            patch.object(snapshot_mod, "get_spotify_client", return_value=sp),
            patch.object(snapshot_mod, "load_schema", return_value=sample_schema),
            patch.object(snapshot_mod, "save_schema"),
        ):
            snapshot_mod.main()

        snap = json.loads(snapshot_path.read_text())
        assert snap["total_playlists"] == 1
