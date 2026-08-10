"""
tests/test_06_playlist_workflow.py

Unit tests for scripts/06_playlist_workflow.py.
All Spotify API calls and classifier calls are mocked — no network required.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Import the script under test (numeric prefix requires importlib)
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def _import_workflow():
    spec = importlib.util.spec_from_file_location(
        "playlist_workflow_06", SCRIPTS_DIR / "06_playlist_workflow.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wf = _import_workflow()
sys.modules.setdefault("playlist_workflow_06", wf)

classify_playlist = wf.classify_playlist
sort_tracks = wf.sort_tracks
_resolve_playlist_id = wf._resolve_playlist_id
_build_subgenre_index = wf._build_subgenre_index


# ---------------------------------------------------------------------------
# Shared fixtures & helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_schema():
    """Schema with two subgenres — one with a playlist ID, one without."""
    return {
        "master_folders": [
            {
                "id": "f1",
                "name": "DJ CHILL / GROOVE",
                "subgenres": [
                    {"name": "Deep House",  "spotify_playlist": "DEEPHOUSE000000000001"},
                    {"name": "Afro House",  "spotify_playlist": None},
                ],
            }
        ]
    }


@pytest.fixture
def minimal_env():
    return {
        "SPOTIFY_CLIENT_ID": "x",
        "SPOTIFY_CLIENT_SECRET": "x",
        "SPOTIFY_REDIRECT_URI": "http://localhost",
        "DISCOGS_TOKEN": "x",
        "LASTFM_API_KEY": "x",
        "LASTFM_SHARED_SECRET": "x",
    }


def _make_classify_result(
    subgenre="Deep House",
    confidence=0.75,
    is_broad_fallback=False,
):
    """Return a mock ClassificationResult."""
    ev = MagicMock()
    ev.provider = "Discogs"
    ev.genres = ["deep house"]

    result = MagicMock()
    result.subgenre = subgenre
    result.confidence = confidence
    result.is_broad_fallback = is_broad_fallback
    result.evidence = [ev]
    return result


def _make_playlist_items_page(tracks, next_url=None):
    """Build a fake sp.playlist_items() page (additional_types=["track"] format)."""
    return {
        "items": [
            {
                "item": {
                    "id": t["id"],
                    "name": t["name"],
                    "artists": [{"name": t["artist"]}],
                }
            }
            for t in tracks
        ],
        "next": next_url,
    }


def _make_classify_results_dict(source_id="SRC00000000000000000001", tracks=None):
    """Build a minimal classify_results dict for sort_tracks tests."""
    if tracks is None:
        tracks = [
            {
                "spotify_track_id": "TRK0000000000000000001",
                "artist": "Black Coffee",
                "title": "Superman",
                "subgenre": "Deep House",
                "confidence": 0.75,
                "is_broad_fallback": False,
                "target_playlist_id": "DEEPHOUSE000000000001",
                "evidence": [],
            }
        ]
    return {
        "source_playlist_id": source_id,
        "source_playlist_name": "unsorted",
        "classified_at": "2026-07-03T12:00:00+00:00",
        "total_tracks": len(tracks),
        "tracks": tracks,
    }


# ===========================================================================
# _resolve_playlist_id
# ===========================================================================


class TestResolvePlaylistId:

    def test_returns_id_directly_when_22_chars_alphanumeric(self):
        sp = MagicMock()
        result = _resolve_playlist_id(sp, "A" * 22)
        sp.current_user_playlists.assert_not_called()
        assert result == "A" * 22

    def test_finds_playlist_by_name(self):
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [
                {"id": "id001", "name": "unsorted"},
                {"id": "id002", "name": "techno"},
            ],
            "next": None,
        }
        result = _resolve_playlist_id(sp, "unsorted")
        assert result == "id001"

    def test_matching_is_case_insensitive(self):
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [{"id": "id001", "name": "Deep House"}],
            "next": None,
        }
        result = _resolve_playlist_id(sp, "deep house")
        assert result == "id001"

    def test_paginates_to_find_playlist(self):
        sp = MagicMock()
        page1 = {"items": [{"id": "id001", "name": "house"}], "next": "url2"}
        page2 = {"items": [{"id": "id002", "name": "unsorted"}], "next": None}
        sp.current_user_playlists.return_value = page1
        sp.next.return_value = page2
        result = _resolve_playlist_id(sp, "unsorted")
        assert result == "id002"

    def test_raises_if_not_found(self):
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [{"id": "id001", "name": "house"}],
            "next": None,
        }
        with pytest.raises(ValueError, match="No Spotify playlist found"):
            _resolve_playlist_id(sp, "missing_playlist")


# ===========================================================================
# classify_playlist — Part 1
# ===========================================================================


@pytest.mark.skip(reason="superseded by metadata-only web research workflow tests")
class TestClassifyPlaylist:

    def _run(self, sp_mock, classifier_mock, env, schema, **kwargs):
        """Helper: patch Spotify client + ConsensusClassifier, then run classify_playlist."""
        with patch("playlist_workflow_06.get_spotify_client", return_value=sp_mock), \
             patch("playlist_workflow_06.ConsensusClassifier", return_value=classifier_mock):
            return classify_playlist(
                "unsorted",
                env=env,
                schema=schema,
                **kwargs,
            )

    def _make_sp(self, tracks, playlist_name="unsorted"):
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [{"id": "SRC0000000000000000001", "name": playlist_name}],
            "next": None,
        }
        sp.playlist.return_value = {"name": playlist_name}
        sp.playlist_items.return_value = _make_playlist_items_page(tracks)
        sp.next.return_value = None
        return sp

    def test_result_has_required_top_level_keys(self, minimal_env, minimal_schema):
        sp = self._make_sp([{"id": "T1", "name": "Superman", "artist": "Black Coffee"}])
        clf = MagicMock()
        clf.classify.return_value = _make_classify_result()

        result = self._run(sp, clf, minimal_env, minimal_schema)

        assert "source_playlist_id" in result
        assert "source_playlist_name" in result
        assert "classified_at" in result
        assert "total_tracks" in result
        assert "tracks" in result

    def test_result_track_has_required_keys(self, minimal_env, minimal_schema):
        sp = self._make_sp([{"id": "T1", "name": "Superman", "artist": "Black Coffee"}])
        clf = MagicMock()
        clf.classify.return_value = _make_classify_result()

        result = self._run(sp, clf, minimal_env, minimal_schema)
        track = result["tracks"][0]

        for key in ("spotify_track_id", "artist", "title", "subgenre",
                    "confidence", "is_broad_fallback", "target_playlist_id", "evidence"):
            assert key in track, f"Missing key: {key}"

    def test_resolves_target_playlist_id_from_schema(self, minimal_env, minimal_schema):
        sp = self._make_sp([{"id": "T1", "name": "Superman", "artist": "Black Coffee"}])
        clf = MagicMock()
        clf.classify.return_value = _make_classify_result(subgenre="Deep House")

        result = self._run(sp, clf, minimal_env, minimal_schema)

        assert result["tracks"][0]["target_playlist_id"] == "DEEPHOUSE000000000001"

    def test_target_playlist_id_is_null_for_subgenre_without_playlist(self, minimal_env, minimal_schema):
        sp = self._make_sp([{"id": "T1", "name": "Afro Track", "artist": "Artist"}])
        clf = MagicMock()
        clf.classify.return_value = _make_classify_result(subgenre="Afro House")

        result = self._run(sp, clf, minimal_env, minimal_schema)

        assert result["tracks"][0]["target_playlist_id"] is None

    def test_target_playlist_id_is_null_when_subgenre_is_none(self, minimal_env, minimal_schema):
        sp = self._make_sp([{"id": "T1", "name": "Unknown", "artist": "Artist"}])
        clf = MagicMock()
        clf.classify.return_value = _make_classify_result(subgenre=None, confidence=0.0)

        result = self._run(sp, clf, minimal_env, minimal_schema)

        assert result["tracks"][0]["subgenre"] is None
        assert result["tracks"][0]["target_playlist_id"] is None

    def test_skips_null_tracks_in_playlist(self, minimal_env, minimal_schema):
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [{"id": "SRC0000000000000000001", "name": "unsorted"}],
            "next": None,
        }
        sp.playlist.return_value = {"name": "unsorted"}
        # One valid track and one null entry (local file)
        sp.playlist_items.return_value = {
            "items": [
                {"item": {"id": "T1", "name": "Superman", "artists": [{"name": "Black Coffee"}]}},
                {"item": None},
                {"item": {"id": None, "name": "Local File", "artists": [{"name": ""}]}},
            ],
            "next": None,
        }
        sp.next.return_value = None
        clf = MagicMock()
        clf.classify.return_value = _make_classify_result()

        with patch("playlist_workflow_06.get_spotify_client", return_value=sp), \
             patch("playlist_workflow_06.ConsensusClassifier", return_value=clf):
            result = classify_playlist("unsorted", env=minimal_env, schema=minimal_schema)

        assert result["total_tracks"] == 1
        assert len(result["tracks"]) == 1

    def test_paginates_all_tracks(self, minimal_env, minimal_schema):
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [{"id": "SRC0000000000000000001", "name": "unsorted"}],
            "next": None,
        }
        sp.playlist.return_value = {"name": "unsorted"}
        page1 = _make_playlist_items_page(
            [{"id": "T1", "name": "Track1", "artist": "Artist1"}],
            next_url="page2",
        )
        page2 = _make_playlist_items_page(
            [{"id": "T2", "name": "Track2", "artist": "Artist2"}],
        )
        sp.playlist_items.return_value = page1
        sp.next.return_value = page2
        clf = MagicMock()
        clf.classify.return_value = _make_classify_result()

        with patch("playlist_workflow_06.get_spotify_client", return_value=sp), \
             patch("playlist_workflow_06.ConsensusClassifier", return_value=clf):
            result = classify_playlist("unsorted", env=minimal_env, schema=minimal_schema)

        assert result["total_tracks"] == 2
        assert clf.classify.call_count == 2

    def test_writes_json_when_output_path_given(self, minimal_env, minimal_schema, tmp_path):
        sp = self._make_sp([{"id": "T1", "name": "Superman", "artist": "Black Coffee"}])
        clf = MagicMock()
        clf.classify.return_value = _make_classify_result()
        out_file = tmp_path / "results.json"

        with patch("playlist_workflow_06.get_spotify_client", return_value=sp), \
             patch("playlist_workflow_06.ConsensusClassifier", return_value=clf):
            classify_playlist(
                "unsorted", env=minimal_env, schema=minimal_schema, output_path=out_file
            )

        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["total_tracks"] == 1
        assert data["tracks"][0]["spotify_track_id"] == "T1"

    def test_confidence_is_rounded_to_4dp(self, minimal_env, minimal_schema):
        sp = self._make_sp([{"id": "T1", "name": "Track", "artist": "Artist"}])
        clf = MagicMock()
        clf.classify.return_value = _make_classify_result(confidence=0.123456789)

        result = self._run(sp, clf, minimal_env, minimal_schema)

        assert result["tracks"][0]["confidence"] == round(0.123456789, 4)


# ===========================================================================
# sort_tracks — Part 2
# ===========================================================================


class TestSortTracks:

    def _run_sort(self, sp_mock, results, env, **kwargs):
        with patch("playlist_workflow_06.get_spotify_client", return_value=sp_mock):
            return sort_tracks(results, env=env, schema={}, **kwargs)

    def _make_sp_with_empty_targets(self):
        sp = MagicMock()
        # Existing tracks page for target playlist = empty
        sp.playlist_items.return_value = {"items": [], "next": None}
        sp.next.return_value = None
        return sp

    # ------------------------------------------------------------------
    # Skip conditions
    # ------------------------------------------------------------------

    def test_skips_track_with_null_subgenre(self, minimal_env):
        results = _make_classify_results_dict(tracks=[{
            "spotify_track_id": "T1", "artist": "A", "title": "B",
            "subgenre": None, "confidence": 0.8,
            "is_broad_fallback": False, "target_playlist_id": "PL1", "evidence": [],
        }])
        sp = self._make_sp_with_empty_targets()
        summary = self._run_sort(sp, results, minimal_env)
        sp._post.assert_not_called()
        assert summary["skipped"]["unclassified"] == 1

    def test_skips_broad_fallback_track(self, minimal_env):
        results = _make_classify_results_dict(tracks=[{
            "spotify_track_id": "T1", "artist": "A", "title": "B",
            "subgenre": "Techno", "confidence": 1.0,
            "is_broad_fallback": True, "target_playlist_id": "PL1", "evidence": [],
        }])
        sp = self._make_sp_with_empty_targets()
        summary = self._run_sort(sp, results, minimal_env)
        sp._post.assert_not_called()
        assert summary["skipped"]["broad_fallback"] == 1

    def test_skips_low_confidence_track(self, minimal_env):
        results = _make_classify_results_dict(tracks=[{
            "spotify_track_id": "T1", "artist": "A", "title": "B",
            "subgenre": "Deep House", "confidence": 0.3,
            "is_broad_fallback": False, "target_playlist_id": "PL1", "evidence": [],
        }])
        sp = self._make_sp_with_empty_targets()
        summary = self._run_sort(sp, results, minimal_env, min_confidence=0.5)
        sp._post.assert_not_called()
        assert summary["skipped"]["low_confidence"] == 1

    def test_skips_track_without_target_playlist(self, minimal_env):
        results = _make_classify_results_dict(tracks=[{
            "spotify_track_id": "T1", "artist": "A", "title": "B",
            "subgenre": "Afro House", "confidence": 0.8,
            "is_broad_fallback": False, "target_playlist_id": None, "evidence": [],
        }])
        sp = self._make_sp_with_empty_targets()
        summary = self._run_sort(sp, results, minimal_env)
        sp._post.assert_not_called()
        assert summary["skipped"]["no_target_playlist"] == 1

    # ------------------------------------------------------------------
    # Happy path — tracks are moved
    # ------------------------------------------------------------------

    def test_adds_track_to_target_playlist(self, minimal_env):
        results = _make_classify_results_dict()  # 1 valid track
        sp = self._make_sp_with_empty_targets()
        summary = self._run_sort(sp, results, minimal_env)
        sp._post.assert_called_once_with(
            "playlists/DEEPHOUSE000000000001/items",
            payload={"uris": ["spotify:track:TRK0000000000000000001"]},
        )
        assert summary["moved"] == 1

    def test_removes_track_from_source_by_default(self, minimal_env):
        results = _make_classify_results_dict()
        sp = self._make_sp_with_empty_targets()
        self._run_sort(sp, results, minimal_env)
        sp._delete.assert_called_once_with(
            "playlists/SRC00000000000000000001/items",
            payload={
                "items": [{"uri": "spotify:track:TRK0000000000000000001"}]
            },
        )

    def test_does_not_remove_from_source_when_flag_false(self, minimal_env):
        results = _make_classify_results_dict()
        sp = self._make_sp_with_empty_targets()
        self._run_sort(sp, results, minimal_env, remove_from_source=False)
        sp._delete.assert_not_called()

    def test_batches_add_in_groups_of_100(self, minimal_env):
        """150 tracks to the same target playlist → 2 add calls (100 + 50)."""
        tracks = [
            {
                "spotify_track_id": f"TRK{i:019d}",
                "artist": "Artist", "title": f"Track {i}",
                "subgenre": "Deep House", "confidence": 0.9,
                "is_broad_fallback": False,
                "target_playlist_id": "DEEPHOUSE000000000001",
                "evidence": [],
            }
            for i in range(150)
        ]
        results = _make_classify_results_dict(tracks=tracks)
        sp = self._make_sp_with_empty_targets()
        self._run_sort(sp, results, minimal_env)
        assert sp._post.call_count == 2
        first_batch = sp._post.call_args_list[0].kwargs["payload"]["uris"]
        second_batch = sp._post.call_args_list[1].kwargs["payload"]["uris"]
        assert len(first_batch) == 100
        assert len(second_batch) == 50

    def test_skips_tracks_already_in_target_playlist(self, minimal_env):
        """Track already in target → not re-added."""
        results = _make_classify_results_dict()
        sp = MagicMock()
        # Target already contains this track
        sp.playlist_items.return_value = {
            "items": [{"item": {"id": "TRK0000000000000000001"}}],
            "next": None,
        }
        sp.next.return_value = None
        self._run_sort(sp, results, minimal_env)
        sp._post.assert_not_called()

    # ------------------------------------------------------------------
    # Dry run
    # ------------------------------------------------------------------

    def test_dry_run_makes_no_write_calls(self, minimal_env):
        results = _make_classify_results_dict()
        with patch("playlist_workflow_06.get_spotify_client") as mock_sp:
            summary = sort_tracks(
                results, env=minimal_env, schema={},
                dry_run=True,
            )
        # No Spotify client should have been created (no write scope needed)
        mock_sp.assert_not_called()
        assert summary["dry_run"] is True
        assert summary["moved"] == 1  # would-be moves still counted

    def test_dry_run_summary_has_correct_structure(self, minimal_env):
        results = _make_classify_results_dict()
        with patch("playlist_workflow_06.get_spotify_client"):
            summary = sort_tracks(results, env=minimal_env, schema={}, dry_run=True)
        assert "moved" in summary
        assert "skipped" in summary
        assert "errors" in summary
        assert "dry_run" in summary

    # ------------------------------------------------------------------
    # Input flexibility
    # ------------------------------------------------------------------

    def test_accepts_json_file_path_as_input(self, minimal_env, tmp_path):
        results = _make_classify_results_dict()
        out_file = tmp_path / "results.json"
        out_file.write_text(json.dumps(results), encoding="utf-8")

        sp = self._make_sp_with_empty_targets()
        with patch("playlist_workflow_06.get_spotify_client", return_value=sp):
            summary = sort_tracks(
                str(out_file), env=minimal_env, schema={}
            )
        assert summary["moved"] == 1

    # ------------------------------------------------------------------
    # Summary dict
    # ------------------------------------------------------------------

    def test_summary_dict_has_all_keys(self, minimal_env):
        results = _make_classify_results_dict()
        sp = self._make_sp_with_empty_targets()
        summary = self._run_sort(sp, results, minimal_env)
        assert "moved" in summary
        assert "skipped" in summary
        for key in ("unclassified", "broad_fallback", "low_confidence", "no_target_playlist"):
            assert key in summary["skipped"]
        assert "errors" in summary
        assert "dry_run" in summary
