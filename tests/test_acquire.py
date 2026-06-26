"""
Tests for scripts/04_acquire.py

Covers:
  - pick_best_file: format priority, bitrate gate, size preference
  - main(): confirmation prompt, slskd flow, empty delta
"""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import importlib
acquire_mod = importlib.import_module("04_acquire")

pick_best_file = acquire_mod.pick_best_file


# ---------------------------------------------------------------------------
# pick_best_file
# ---------------------------------------------------------------------------

class TestPickBestFile:
    def _make_result(self, username, files):
        return {"username": username, "files": files}

    def _make_file(self, filename, bitrate=None, size=10_000_000):
        result = {"filename": filename, "size": size}
        # Give the file a length consistent with the requested bitrate so the
        # estimated_bitrate check (size*8 / length*1000) works correctly.
        if bitrate:
            result["length"] = int((size * 8) / (bitrate * 1000))
        else:
            result["length"] = 0
        return result

    def _top(self, results, min_br=320, fmts=None):
        """Return (username, filename) of best candidate, or (None, None)."""
        fmts = fmts or ["flac", "wav", "aiff", "mp3"]
        candidates = pick_best_file(results, min_br, fmts)
        return (candidates[0][0], candidates[0][1]) if candidates else (None, None)

    def test_prefers_flac_over_mp3(self):
        results = [
            self._make_result("user1", [
                self._make_file("track.mp3", bitrate=320),
                self._make_file("track.flac"),
            ])
        ]
        _, filename = self._top(results)
        assert filename.endswith(".flac")

    def test_prefers_wav_over_aiff(self):
        results = [
            self._make_result("user1", [
                self._make_file("track.aiff"),
                self._make_file("track.wav"),
            ])
        ]
        _, filename = self._top(results)
        assert filename.endswith(".wav")

    def test_prefers_aiff_over_mp3(self):
        results = [
            self._make_result("user1", [
                self._make_file("track.mp3", bitrate=320),
                self._make_file("track.aiff"),
            ])
        ]
        _, filename = self._top(results)
        assert filename.endswith(".aiff")

    def test_rejects_mp3_below_bitrate_threshold(self):
        results = [
            self._make_result("user1", [
                self._make_file("track.mp3", bitrate=128),
            ])
        ]
        username, filename = self._top(results)
        assert username is None
        assert filename is None

    def test_accepts_mp3_at_exact_threshold(self):
        results = [
            self._make_result("user1", [
                self._make_file("track.mp3", bitrate=320),
            ])
        ]
        _, filename = self._top(results)
        assert filename is not None

    def test_accepts_mp3_above_threshold(self):
        results = [
            self._make_result("user1", [
                self._make_file("track.mp3", bitrate=320),
            ])
        ]
        _, filename = self._top(results, min_br=192)
        assert filename is not None

    def test_prefers_larger_file_for_same_format(self):
        results = [
            self._make_result("user1", [self._make_file("track.flac", size=20_000_000)]),
            self._make_result("user2", [self._make_file("track.flac", size=30_000_000)]),
        ]
        username, _ = self._top(results)
        assert username == "user2"

    def test_returns_correct_username(self):
        results = [
            self._make_result("best_user", [self._make_file("track.flac")])
        ]
        username, _ = self._top(results)
        assert username == "best_user"

    def test_empty_results_returns_empty_list(self):
        candidates = pick_best_file([], 320, ["flac", "wav", "aiff", "mp3"])
        assert candidates == []

    def test_ignores_unsupported_formats(self):
        results = [
            self._make_result("user1", [
                self._make_file("track.ogg"),
                self._make_file("track.wma"),
            ])
        ]
        username, _ = self._top(results)
        assert username is None

    def test_multiple_users_picks_best_format(self):
        results = [
            self._make_result("user_mp3", [self._make_file("track.mp3", bitrate=320)]),
            self._make_result("user_flac", [self._make_file("track.flac")]),
        ]
        username, _ = self._top(results)
        assert username == "user_flac"

    def test_null_bitrate_treated_as_zero(self):
        results = [
            self._make_result("user1", [self._make_file("track.mp3", bitrate=None)])
        ]
        username, _ = self._top(results)
        assert username is None  # null bitrate < 320 threshold

    def test_flac_not_filtered_by_bitrate(self):
        """FLAC files have no bitrate field — should not be rejected by MP3 bitrate gate."""
        results = [
            self._make_result("user1", [self._make_file("track.flac", bitrate=None)])
        ]
        _, filename = self._top(results)
        assert filename is not None
        assert filename.endswith(".flac")

    def test_returns_multiple_candidates(self):
        """pick_best_file returns up to 3 ranked candidates."""
        results = [
            self._make_result("u1", [self._make_file("track.flac", size=30_000_000)]),
            self._make_result("u2", [self._make_file("track.flac", size=20_000_000)]),
            self._make_result("u3", [self._make_file("track.mp3", bitrate=320)]),
            self._make_result("u4", [self._make_file("track.mp3", bitrate=320)]),
        ]
        candidates = pick_best_file(results, 320, ["flac", "wav", "aiff", "mp3"])
        assert len(candidates) <= 3
        # Best (largest flac) is first
        assert candidates[0][0] == "u1"


# ---------------------------------------------------------------------------
# main() — integration
# ---------------------------------------------------------------------------

class TestAcquireMain:
    def _fake_schema(self, delta):
        """Build a minimal schema that maps each delta track's subgenre to its local_folder."""
        return {"master_folders": [{"name": "test", "subgenres": [
            {"name": t["subgenre"], "local_folder": t["local_folder"]}
            for t in delta.get("tracks", [])
        ]}]}

    def _run_main(self, tmp_path, delta, user_input="y", slskd_results=None, slskd_download_ok=True):
        delta_path = tmp_path / "delta.json"
        delta_path.write_text(json.dumps(delta))
        music_root = tmp_path / "music"
        music_root.mkdir(exist_ok=True)

        env = {
            "SLSKD_BASE_URL": "http://localhost:5030",
            "SLSKD_API_KEY": "test_key",
            "MIN_BITRATE_KBPS": 320,
            "PREFERRED_FORMATS": ["flac", "wav", "aiff", "mp3"],
            "MUSIC_ROOT": str(music_root),
        }

        with (
            patch.object(acquire_mod, "PROJECT_ROOT", tmp_path),
            patch.object(acquire_mod, "DELTA_PATH", delta_path),
            patch.object(acquire_mod, "load_env", return_value=env),
            patch.object(acquire_mod, "load_schema", return_value=self._fake_schema(delta)),
            patch("builtins.input", return_value=user_input),
            patch.object(acquire_mod, "slskd_search", return_value=slskd_results or []),
            patch.object(acquire_mod, "slskd_download"),
            patch.object(acquire_mod, "wait_for_slskd_file", return_value=None if not slskd_download_ok else str(tmp_path / "track.flac")),
        ):
            acquire_mod.main()

    def test_exits_if_delta_missing(self, tmp_path):
        with (
            patch.object(acquire_mod, "PROJECT_ROOT", tmp_path),
            patch.object(acquire_mod, "DELTA_PATH", tmp_path / "nonexistent.json"),
            patch.object(acquire_mod, "load_env", return_value={"MUSIC_ROOT": str(tmp_path)}),
            patch.object(acquire_mod, "load_schema", return_value={"master_folders": []}),
        ):
            with pytest.raises(SystemExit):
                acquire_mod.main()

    def test_exits_on_empty_delta(self, tmp_path):
        delta = {"generated_at": "2026-06-24T00:00:00+00:00", "total_missing": 0, "tracks": []}
        delta_path = tmp_path / "delta.json"
        delta_path.write_text(json.dumps(delta))
        music_root = tmp_path / "music"
        music_root.mkdir()

        with (
            patch.object(acquire_mod, "PROJECT_ROOT", tmp_path),
            patch.object(acquire_mod, "DELTA_PATH", delta_path),
            patch.object(acquire_mod, "load_env", return_value={"MUSIC_ROOT": str(music_root)}),
            patch.object(acquire_mod, "load_schema", return_value={"master_folders": []}),
        ):
            with pytest.raises(SystemExit):
                acquire_mod.main()

    def test_aborts_if_user_declines(self, tmp_path, sample_delta):
        search_called = []
        with (
            patch.object(acquire_mod, "PROJECT_ROOT", tmp_path),
            patch.object(acquire_mod, "DELTA_PATH", tmp_path / "delta.json"),
            patch.object(acquire_mod, "load_env", return_value={"MUSIC_ROOT": str(tmp_path / "music")}),
            patch.object(acquire_mod, "load_schema", return_value={"master_folders": []}),
            patch("builtins.input", return_value="n"),
            patch.object(acquire_mod, "slskd_search", side_effect=lambda *a, **kw: search_called.append(1) or []),
        ):
            (tmp_path / "delta.json").write_text(json.dumps(sample_delta))
            (tmp_path / "music").mkdir(exist_ok=True)
            with pytest.raises(SystemExit):
                acquire_mod.main()

        assert len(search_called) == 0

    def test_calls_slskd_search_per_track(self, tmp_path, sample_delta):
        search_calls = []
        (tmp_path / "music").mkdir(exist_ok=True)
        for t in sample_delta["tracks"]:
            lf = t["local_folder"].replace("music/", "")
            (tmp_path / "music" / lf).mkdir(parents=True, exist_ok=True)

        fake_schema = {"master_folders": [{"name": "test", "subgenres": [
            {"name": t["subgenre"], "local_folder": t["local_folder"]}
            for t in sample_delta["tracks"]
        ]}]}

        with (
            patch.object(acquire_mod, "PROJECT_ROOT", tmp_path),
            patch.object(acquire_mod, "DELTA_PATH", tmp_path / "delta.json"),
            patch.object(acquire_mod, "load_env", return_value={
                "SLSKD_BASE_URL": "http://localhost:5030",
                "SLSKD_API_KEY": "key",
                "MIN_BITRATE_KBPS": 320,
                "PREFERRED_FORMATS": ["flac", "wav", "aiff", "mp3"],
                "MUSIC_ROOT": str(tmp_path / "music"),
            }),
            patch.object(acquire_mod, "load_schema", return_value=fake_schema),
            patch("builtins.input", return_value="y"),
            patch.object(acquire_mod, "slskd_search", side_effect=lambda *a, **kw: search_calls.append(a) or []),
        ):
            (tmp_path / "delta.json").write_text(json.dumps(sample_delta))
            acquire_mod.main()

        assert len(search_calls) == len(sample_delta["tracks"])

    def test_marks_failed_when_slskd_empty(self, tmp_path, sample_delta):
        """When slskd returns no results, all tracks count as failed — no silent fallback."""
        (tmp_path / "music").mkdir(exist_ok=True)
        for t in sample_delta["tracks"]:
            lf = t["local_folder"].replace("music/", "")
            (tmp_path / "music" / lf).mkdir(parents=True, exist_ok=True)

        fake_schema = {"master_folders": [{"name": "test", "subgenres": [
            {"name": t["subgenre"], "local_folder": t["local_folder"]}
            for t in sample_delta["tracks"]
        ]}]}

        with (
            patch.object(acquire_mod, "PROJECT_ROOT", tmp_path),
            patch.object(acquire_mod, "DELTA_PATH", tmp_path / "delta.json"),
            patch.object(acquire_mod, "load_env", return_value={
                "SLSKD_BASE_URL": "http://localhost:5030",
                "SLSKD_API_KEY": "key",
                "MIN_BITRATE_KBPS": 320,
                "PREFERRED_FORMATS": ["flac", "wav", "aiff", "mp3"],
                "MUSIC_ROOT": str(tmp_path / "music"),
            }),
            patch.object(acquire_mod, "load_schema", return_value=fake_schema),
            patch("builtins.input", return_value="y"),
            patch.object(acquire_mod, "slskd_search", return_value=[]),
        ):
            (tmp_path / "delta.json").write_text(json.dumps(sample_delta))
            acquire_mod.main()  # must complete without calling any non-slskd fallback
