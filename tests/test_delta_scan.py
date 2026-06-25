"""
Tests for scripts/03_delta_scan.py
"""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import importlib
delta_mod = importlib.import_module("03_delta_scan")

normalize = delta_mod.normalize
load_local_stems = delta_mod.load_local_stems
is_local_match = delta_mod.is_local_match


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_lowercases(self):
        assert normalize("HELLO WORLD") == "hello world"

    def test_strips_special_chars(self):
        assert normalize("hello, world!") == "hello world"

    def test_strips_hyphens(self):
        # hyphens are stripped; surrounding spaces collapse to one space
        assert normalize("artist - title") == "artist  title".replace("  ", " ")

    def test_collapses_whitespace(self):
        assert normalize("hello   world") == "hello world"

    def test_strips_leading_trailing_whitespace(self):
        assert normalize("  hello  ") == "hello"

    def test_empty_string(self):
        assert normalize("") == ""

    def test_preserves_numbers(self):
        assert normalize("track 4 life") == "track 4 life"

    def test_strips_ampersand(self):
        result = normalize("vintage culture & gabss")
        assert "&" not in result

    def test_strips_parentheses(self):
        result = normalize("lost (original mix)")
        assert "(" not in result
        assert ")" not in result


# ---------------------------------------------------------------------------
# load_local_stems
# ---------------------------------------------------------------------------

class TestLoadLocalStems:
    def test_returns_stems_for_audio_files(self, tmp_path):
        (tmp_path / "Artist A - Track One.mp3").touch()
        (tmp_path / "Artist B - Track Two.flac").touch()
        stems = load_local_stems(tmp_path)
        assert len(stems) == 2

    def test_ignores_non_audio_files(self, tmp_path):
        (tmp_path / "artwork.jpg").touch()
        (tmp_path / "info.txt").touch()
        (tmp_path / "Artist - Track.mp3").touch()
        stems = load_local_stems(tmp_path)
        assert len(stems) == 1

    def test_supports_all_audio_extensions(self, tmp_path):
        for ext in [".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a"]:
            (tmp_path / f"track{ext}").touch()
        stems = load_local_stems(tmp_path)
        assert len(stems) == 6

    def test_stems_are_normalized(self, tmp_path):
        (tmp_path / "Black Coffee - Kawa.mp3").touch()
        stems = load_local_stems(tmp_path)
        # normalize strips hyphens and collapses spaces
        assert stems[0] == normalize("Black Coffee - Kawa")

    def test_returns_empty_for_nonexistent_folder(self, tmp_path):
        stems = load_local_stems(tmp_path / "nonexistent")
        assert stems == []

    def test_returns_empty_for_empty_folder(self, tmp_path):
        stems = load_local_stems(tmp_path)
        assert stems == []

    def test_ignores_subdirectories(self, tmp_path):
        (tmp_path / "subfolder").mkdir()
        (tmp_path / "track.mp3").touch()
        stems = load_local_stems(tmp_path)
        assert len(stems) == 1


# ---------------------------------------------------------------------------
# is_local_match
# ---------------------------------------------------------------------------

class TestIsLocalMatch:
    def test_exact_match(self, tmp_path):
        (tmp_path / "Black Coffee - Kawa.mp3").touch()
        stems = load_local_stems(tmp_path)
        assert is_local_match(stems, "Black Coffee", "Kawa")

    def test_no_match(self, tmp_path):
        (tmp_path / "Black Coffee - Kawa.mp3").touch()
        stems = load_local_stems(tmp_path)
        assert not is_local_match(stems, "Victor Ruiz", "Love Story")

    def test_case_insensitive_match(self, tmp_path):
        (tmp_path / "black coffee - kawa.mp3").touch()
        stems = load_local_stems(tmp_path)
        assert is_local_match(stems, "BLACK COFFEE", "KAWA")

    def test_minor_variation_matches(self, tmp_path):
        # "Enoo Napa" vs "enoo napa" slight punctuation difference
        (tmp_path / "Enoo Napa - Superman.mp3").touch()
        stems = load_local_stems(tmp_path)
        assert is_local_match(stems, "Enoo Napa", "Superman")

    def test_empty_folder_no_match(self):
        assert not is_local_match([], "Artist", "Title")

    def test_threshold_respected(self, tmp_path):
        # Completely different track should not match even below threshold
        (tmp_path / "DJ Koze - Pick Up.flac").touch()
        stems = load_local_stems(tmp_path)
        assert not is_local_match(stems, "Victor Ruiz", "Love Story")

    def test_ampersand_vs_and_matches(self, tmp_path):
        # "Vintage Culture & Gabss" vs "Vintage Culture and Gabss" should fuzzy-match
        (tmp_path / "Vintage Culture & Gabss - Lost.mp3").touch()
        stems = load_local_stems(tmp_path)
        assert is_local_match(stems, "Vintage Culture and Gabss", "Lost")


# ---------------------------------------------------------------------------
# main() — integration
# ---------------------------------------------------------------------------

class TestDeltaScanMain:
    def _run(self, tmp_path, snapshot, music_root):
        snapshot_path = tmp_path / "spotify_snapshot.json"
        delta_path = tmp_path / "delta.json"
        snapshot_path.write_text(json.dumps(snapshot))

        with (
            patch.object(delta_mod, "PROJECT_ROOT", tmp_path),
            patch.object(delta_mod, "SNAPSHOT_PATH", snapshot_path),
            patch.object(delta_mod, "DELTA_PATH", delta_path),
            patch.object(delta_mod, "load_env", return_value={"MUSIC_ROOT": str(music_root)}),
        ):
            delta_mod.main()

        return json.loads(delta_path.read_text())

    def test_correctly_identifies_missing_tracks(self, tmp_path, sample_snapshot):
        music_root = tmp_path / "music"
        (music_root / "afro house").mkdir(parents=True)
        (music_root / "tech house").mkdir(parents=True)
        # Place only one of the afro house tracks locally
        (music_root / "afro house" / "Black Coffee - Kawa.mp3").touch()

        delta = self._run(tmp_path, sample_snapshot, music_root)

        artists = [t["artist"] for t in delta["tracks"]]
        assert "Black Coffee" not in artists   # present locally
        assert "Enoo Napa" in artists          # missing
        assert "Artist B" in artists           # missing

    def test_all_tracks_missing_when_folder_empty(self, tmp_path, sample_snapshot):
        music_root = tmp_path / "music"
        (music_root / "afro house").mkdir(parents=True)
        (music_root / "tech house").mkdir(parents=True)

        delta = self._run(tmp_path, sample_snapshot, music_root)
        assert delta["total_missing"] == 5  # all tracks in sample_snapshot

    def test_no_delta_when_all_present(self, tmp_path, sample_snapshot):
        music_root = tmp_path / "music"
        (music_root / "afro house").mkdir(parents=True)
        (music_root / "tech house").mkdir(parents=True)
        # Create all tracks as local files
        for pl in sample_snapshot["playlists"]:
            folder = music_root / pl["local_folder"].split("/", 1)[1]
            for t in pl["tracks"]:
                (folder / f"{t['artist']} - {t['title']}.mp3").touch()

        delta = self._run(tmp_path, sample_snapshot, music_root)
        assert delta["total_missing"] == 0
        assert delta["tracks"] == []

    def test_delta_track_has_required_fields(self, tmp_path, sample_snapshot):
        music_root = tmp_path / "music"
        (music_root / "afro house").mkdir(parents=True)
        (music_root / "tech house").mkdir(parents=True)

        delta = self._run(tmp_path, sample_snapshot, music_root)
        for track in delta["tracks"]:
            assert "artist" in track
            assert "title" in track
            assert "spotify_id" in track
            assert "subgenre" in track
            assert "master_folder" in track
            assert "local_folder" in track

    def test_exits_if_snapshot_missing(self, tmp_path):
        with (
            patch.object(delta_mod, "PROJECT_ROOT", tmp_path),
            patch.object(delta_mod, "SNAPSHOT_PATH", tmp_path / "nonexistent.json"),
            patch.object(delta_mod, "load_env", return_value={"MUSIC_ROOT": str(tmp_path / "music")}),
        ):
            with pytest.raises(SystemExit):
                delta_mod.main()

    def test_handles_null_local_folder(self, tmp_path):
        """Playlists with local_folder: null should derive path from subgenre name."""
        snapshot = {
            "generated_at": "2026-06-24T00:00:00+00:00",
            "total_playlists": 1,
            "playlists": [{
                "playlist_id": "p1",
                "playlist_name": "Melodic House",
                "schema_subgenre": "Melodic House",
                "master_folder": "DJ CHILL / GROOVE",
                "local_folder": None,
                "tracks": [{"id": "t1", "title": "Sunrise", "artist": "Lane 8", "duration_ms": 300000}],
            }],
        }
        music_root = tmp_path / "music"
        music_root.mkdir()

        delta = self._run(tmp_path, snapshot, music_root)
        assert delta["total_missing"] == 1
        assert delta["tracks"][0]["artist"] == "Lane 8"

    def test_writes_generated_at_timestamp(self, tmp_path, sample_snapshot):
        music_root = tmp_path / "music"
        (music_root / "afro house").mkdir(parents=True)
        (music_root / "tech house").mkdir(parents=True)

        delta = self._run(tmp_path, sample_snapshot, music_root)
        assert "generated_at" in delta
        assert "T" in delta["generated_at"]  # ISO format
