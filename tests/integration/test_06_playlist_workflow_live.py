"""
tests/integration/test_06_playlist_workflow_live.py

Integration tests for scripts/06_playlist_workflow.py.
Makes real Spotify API calls — requires valid credentials in .env.

Test playlist: "Test Playlist"  (public, never modified)
ID: 3FCiuZ8yhAu7iTf5k41Vio

Expected tracks:
  Funk Tribu  — I Got It For You
  Funk Tribu  — Phonky Tribu
  KI/KI       — What's a Girl to Do in '25
  Odymel      — Gucci
  Odymel      — Thank You (Rework)

Run with:
    pytest tests/integration/test_06_playlist_workflow_live.py -m integration -v -s
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

pytestmark = pytest.mark.integration

SCRIPTS_DIR = Path(__file__).parents[2] / "scripts"

# ---------------------------------------------------------------------------
# Import script under test
# ---------------------------------------------------------------------------


def _import_workflow():
    spec = importlib.util.spec_from_file_location(
        "playlist_workflow_06_live", SCRIPTS_DIR / "06_playlist_workflow.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wf = _import_workflow()

# ---------------------------------------------------------------------------
# Playlist to test against (public, never modified)
# ---------------------------------------------------------------------------

TEST_PLAYLIST_ID   = "3FCiuZ8yhAu7iTf5k41Vio"
TEST_PLAYLIST_NAME = "Test Playlist"

EXPECTED_TRACKS = [
    {"id": "4HYWGaqCkshGWu6stZxlmd", "artist": "Funk Tribu", "title": "I Got It For You"},
    {"id": "7aIb17DMLcOhLJIc9vF6Aa", "artist": "Funk Tribu", "title": "Phonky Tribu"},
    {"id": "3OgdqZIgGIqusstV9IqX6p", "artist": "KI/KI",      "title": "What's a Girl to Do in '25"},
    {"id": "5jWqlILvwGMwsk3ZKfhxPU", "artist": "Odymel",     "title": "Gucci"},
    {"id": "5bcvu6T9QzdNFxTy6FZ0WQ", "artist": "Odymel",     "title": "Thank You (Rework)"},
]


# ---------------------------------------------------------------------------
# Module-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def env():
    from utils.config import load_env
    return load_env()


@pytest.fixture(scope="module")
def schema():
    from utils.config import load_schema
    return load_schema()


@pytest.fixture(scope="module")
def classify_results(env, schema):
    """Run classify_playlist once using the playlist ID; share across all tests."""
    return wf.classify_playlist(TEST_PLAYLIST_ID, env=env, schema=schema)


# ---------------------------------------------------------------------------
# TestClassifyPlaylistLive
# ---------------------------------------------------------------------------


class TestClassifyPlaylistLive:

    def test_result_has_required_top_level_keys(self, classify_results):
        for key in ("source_playlist_id", "source_playlist_name",
                    "classified_at", "total_tracks", "tracks"):
            assert key in classify_results, f"Missing key: {key}"

    def test_source_playlist_matches_expected(self, classify_results):
        assert classify_results["source_playlist_id"] == TEST_PLAYLIST_ID
        assert classify_results["source_playlist_name"] == TEST_PLAYLIST_NAME

    def test_total_tracks_equals_expected(self, classify_results):
        print(f"\n[classify] total_tracks = {classify_results['total_tracks']}")
        assert classify_results["total_tracks"] == len(EXPECTED_TRACKS)

    def test_all_expected_track_ids_are_present(self, classify_results):
        found_ids = {t["spotify_track_id"] for t in classify_results["tracks"]}
        for expected in EXPECTED_TRACKS:
            assert expected["id"] in found_ids, (
                f"Expected track missing: {expected['artist']} — {expected['title']}"
            )

    def test_artist_and_title_match_expected(self, classify_results):
        by_id = {t["spotify_track_id"]: t for t in classify_results["tracks"]}
        for expected in EXPECTED_TRACKS:
            track = by_id[expected["id"]]
            assert track["artist"] == expected["artist"]
            assert track["title"]  == expected["title"]

    def test_every_track_has_required_keys(self, classify_results):
        required = (
            "spotify_track_id", "artist", "title",
            "subgenre", "confidence", "is_broad_fallback",
            "target_playlist_id", "evidence",
        )
        for i, track in enumerate(classify_results["tracks"]):
            for key in required:
                assert key in track, f"Track {i} missing key: {key}"

    def test_confidence_is_between_0_and_1(self, classify_results):
        for track in classify_results["tracks"]:
            assert 0.0 <= track["confidence"] <= 1.0, (
                f"Confidence out of range for {track['artist']} — {track['title']}: "
                f"{track['confidence']}"
            )

    def test_target_playlist_id_consistent_with_subgenre(self, classify_results, schema):
        """If subgenre is set and known in the schema, target_playlist_id must match."""
        # Build index from schema
        index: dict[str, str | None] = {}
        for folder in schema.get("master_folders", []):
            for sg in folder.get("subgenres", []):
                index[sg["name"]] = sg.get("spotify_playlist")

        for track in classify_results["tracks"]:
            sg = track["subgenre"]
            if sg and sg in index:
                expected = index[sg]
                assert track["target_playlist_id"] == expected, (
                    f"target_playlist_id mismatch for {track['title']!r}: "
                    f"expected {expected!r}, got {track['target_playlist_id']!r}"
                )

    def test_print_all_classifications(self, classify_results):
        """Observation test — always passes; prints the full classification table."""
        print(f"\nPlaylist: {classify_results['source_playlist_name']!r}  (ID: {classify_results['source_playlist_id']})")
        print(f"Total tracks: {classify_results['total_tracks']}")
        print()
        for t in classify_results["tracks"]:
            subgenre = t["subgenre"] or "— unclassified"
            broad = " [broad]" if t["is_broad_fallback"] else ""
            target = t["target_playlist_id"] or "no target"
            print(
                f"  {t['artist']:<30} {t['title']:<35} "
                f"{subgenre:<22}{broad}  ({t['confidence']:.0%})  → {target}"
            )


# ---------------------------------------------------------------------------
# TestSortTracksDryRunLive
# ---------------------------------------------------------------------------


class TestSortTracksDryRunLive:
    """Dry-run sort against the real classified results — no Spotify writes."""

    def test_dry_run_completes_without_error(self, classify_results, env, schema):
        summary = wf.sort_tracks(
            classify_results,
            env=env,
            schema=schema,
            min_confidence=0.5,
            dry_run=True,
        )
        assert "moved" in summary
        assert "skipped" in summary
        assert "errors" in summary
        assert summary["dry_run"] is True

    def test_dry_run_moved_plus_skipped_equals_total(self, classify_results, env, schema):
        summary = wf.sort_tracks(
            classify_results,
            env=env,
            schema=schema,
            min_confidence=0.5,
            dry_run=True,
        )
        total_accounted = summary["moved"] + sum(summary["skipped"].values())
        assert total_accounted == classify_results["total_tracks"], (
            f"moved ({summary['moved']}) + skipped ({sum(summary['skipped'].values())}) "
            f"!= total_tracks ({classify_results['total_tracks']})"
        )

    def test_print_sort_summary(self, classify_results, env, schema):
        """Observation test — always passes; prints what would be moved."""
        summary = wf.sort_tracks(
            classify_results,
            env=env,
            schema=schema,
            min_confidence=0.5,
            dry_run=True,
        )
        print(f"\nSort dry-run summary:")
        print(f"  Would move:  {summary['moved']}")
        for reason, count in summary["skipped"].items():
            if count:
                print(f"  Skipped ({reason}): {count}")
