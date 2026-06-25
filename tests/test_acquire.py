"""
Tests for scripts/04_acquire.py

Covers:
  - pick_best_file: format priority, bitrate gate, size preference
  - ytdlp_download: subprocess command construction
  - main(): confirmation prompt, slskd flow, yt-dlp fallback, empty delta
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
ytdlp_download = acquire_mod.ytdlp_download


# ---------------------------------------------------------------------------
# pick_best_file
# ---------------------------------------------------------------------------

class TestPickBestFile:
    def _make_result(self, username, files):
        return {"username": username, "files": files}

    def _make_file(self, filename, bitrate=None, size=10_000_000):
        return {"filename": filename, "bitRate": bitrate, "size": size}

    def test_prefers_flac_over_mp3(self):
        results = [
            self._make_result("user1", [
                self._make_file("track.mp3", bitrate=320),
                self._make_file("track.flac"),
            ])
        ]
        username, filename = pick_best_file(results, 320, ["flac", "wav", "aiff", "mp3"])
        assert filename.endswith(".flac")

    def test_prefers_wav_over_aiff(self):
        results = [
            self._make_result("user1", [
                self._make_file("track.aiff"),
                self._make_file("track.wav"),
            ])
        ]
        username, filename = pick_best_file(results, 320, ["flac", "wav", "aiff", "mp3"])
        assert filename.endswith(".wav")

    def test_prefers_aiff_over_mp3(self):
        results = [
            self._make_result("user1", [
                self._make_file("track.mp3", bitrate=320),
                self._make_file("track.aiff"),
            ])
        ]
        username, filename = pick_best_file(results, 320, ["flac", "wav", "aiff", "mp3"])
        assert filename.endswith(".aiff")

    def test_rejects_mp3_below_bitrate_threshold(self):
        results = [
            self._make_result("user1", [
                self._make_file("track.mp3", bitrate=128),
            ])
        ]
        username, filename = pick_best_file(results, 320, ["flac", "wav", "aiff", "mp3"])
        assert username is None
        assert filename is None

    def test_accepts_mp3_at_exact_threshold(self):
        results = [
            self._make_result("user1", [
                self._make_file("track.mp3", bitrate=320),
            ])
        ]
        username, filename = pick_best_file(results, 320, ["flac", "wav", "aiff", "mp3"])
        assert filename is not None

    def test_accepts_mp3_above_threshold(self):
        results = [
            self._make_result("user1", [
                self._make_file("track.mp3", bitrate=320),
            ])
        ]
        username, filename = pick_best_file(results, 192, ["flac", "wav", "aiff", "mp3"])
        assert filename is not None

    def test_prefers_larger_file_for_same_format(self):
        results = [
            self._make_result("user1", [self._make_file("track.flac", size=20_000_000)]),
            self._make_result("user2", [self._make_file("track.flac", size=30_000_000)]),
        ]
        username, filename = pick_best_file(results, 320, ["flac", "wav", "aiff", "mp3"])
        assert username == "user2"

    def test_returns_correct_username(self):
        results = [
            self._make_result("best_user", [self._make_file("track.flac")])
        ]
        username, filename = pick_best_file(results, 320, ["flac", "wav", "aiff", "mp3"])
        assert username == "best_user"

    def test_empty_results_returns_none(self):
        username, filename = pick_best_file([], 320, ["flac", "wav", "aiff", "mp3"])
        assert username is None
        assert filename is None

    def test_ignores_unsupported_formats(self):
        results = [
            self._make_result("user1", [
                self._make_file("track.ogg"),
                self._make_file("track.wma"),
            ])
        ]
        username, filename = pick_best_file(results, 320, ["flac", "wav", "aiff", "mp3"])
        assert username is None

    def test_multiple_users_picks_best_format(self):
        results = [
            self._make_result("user_mp3", [self._make_file("track.mp3", bitrate=320)]),
            self._make_result("user_flac", [self._make_file("track.flac")]),
        ]
        username, filename = pick_best_file(results, 320, ["flac", "wav", "aiff", "mp3"])
        assert username == "user_flac"

    def test_null_bitrate_treated_as_zero(self):
        results = [
            self._make_result("user1", [self._make_file("track.mp3", bitrate=None)])
        ]
        username, filename = pick_best_file(results, 320, ["flac", "wav", "aiff", "mp3"])
        assert username is None  # null bitrate < 320 threshold

    def test_flac_not_filtered_by_bitrate(self):
        """FLAC files have no bitrate field — should not be rejected by MP3 bitrate gate."""
        results = [
            self._make_result("user1", [self._make_file("track.flac", bitrate=None)])
        ]
        username, filename = pick_best_file(results, 320, ["flac", "wav", "aiff", "mp3"])
        assert filename is not None
        assert filename.endswith(".flac")


# ---------------------------------------------------------------------------
# ytdlp_download
# ---------------------------------------------------------------------------

class TestYtdlpDownload:
    def test_calls_yt_dlp_with_correct_query(self, tmp_path):
        with patch.object(acquire_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            ytdlp_download("Victor Ruiz", "Love Story", tmp_path)

        args = mock_run.call_args[0][0]
        assert "yt-dlp" in args
        assert "ytsearch1:Victor Ruiz - Love Story" in args

    def test_uses_extract_audio_flag(self, tmp_path):
        with patch.object(acquire_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            ytdlp_download("Artist", "Title", tmp_path)

        args = mock_run.call_args[0][0]
        assert "--extract-audio" in args

    def test_sets_audio_format_mp3(self, tmp_path):
        with patch.object(acquire_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            ytdlp_download("Artist", "Title", tmp_path)

        args = mock_run.call_args[0][0]
        assert "--audio-format" in args
        idx = args.index("--audio-format")
        assert args[idx + 1] == "mp3"

    def test_returns_true_on_success(self, tmp_path):
        with patch.object(acquire_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = ytdlp_download("Artist", "Title", tmp_path)
        assert result is True

    def test_returns_false_on_failure(self, tmp_path):
        with patch.object(acquire_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = ytdlp_download("Artist", "Title", tmp_path)
        assert result is False

    def test_creates_dest_folder_if_missing(self, tmp_path):
        dest = tmp_path / "new_folder"
        with patch.object(acquire_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            ytdlp_download("Artist", "Title", dest)
        assert dest.exists()

    def test_no_playlist_flag_present(self, tmp_path):
        with patch.object(acquire_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            ytdlp_download("Artist", "Title", tmp_path)

        args = mock_run.call_args[0][0]
        assert "--no-playlist" in args


# ---------------------------------------------------------------------------
# main() — integration
# ---------------------------------------------------------------------------

class TestAcquireMain:
    def _run_main(self, tmp_path, delta, user_input="y", slskd_results=None, slskd_download_ok=True, ytdlp_ok=True):
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
            patch("builtins.input", return_value=user_input),
            patch.object(acquire_mod, "slskd_search", return_value=slskd_results or []),
            patch.object(acquire_mod, "slskd_download"),
            patch.object(acquire_mod, "wait_for_slskd_file", return_value=None if not slskd_download_ok else str(tmp_path / "track.flac")),
            patch.object(acquire_mod, "ytdlp_download", return_value=ytdlp_ok),
        ):
            acquire_mod.main()

    def test_exits_if_delta_missing(self, tmp_path):
        with (
            patch.object(acquire_mod, "PROJECT_ROOT", tmp_path),
            patch.object(acquire_mod, "DELTA_PATH", tmp_path / "nonexistent.json"),
            patch.object(acquire_mod, "load_env", return_value={"MUSIC_ROOT": str(tmp_path)}),
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
        ):
            with pytest.raises(SystemExit):
                acquire_mod.main()

    def test_aborts_if_user_declines(self, tmp_path, sample_delta):
        search_called = []
        with (
            patch.object(acquire_mod, "PROJECT_ROOT", tmp_path),
            patch.object(acquire_mod, "DELTA_PATH", tmp_path / "delta.json"),
            patch.object(acquire_mod, "load_env", return_value={"MUSIC_ROOT": str(tmp_path / "music")}),
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
            patch("builtins.input", return_value="y"),
            patch.object(acquire_mod, "slskd_search", side_effect=lambda *a, **kw: search_calls.append(a) or []),
            patch.object(acquire_mod, "ytdlp_download", return_value=True),
        ):
            (tmp_path / "delta.json").write_text(json.dumps(sample_delta))
            acquire_mod.main()

        assert len(search_calls) == len(sample_delta["tracks"])

    def test_falls_back_to_ytdlp_when_slskd_empty(self, tmp_path, sample_delta):
        ytdlp_calls = []
        (tmp_path / "music").mkdir(exist_ok=True)
        for t in sample_delta["tracks"]:
            lf = t["local_folder"].replace("music/", "")
            (tmp_path / "music" / lf).mkdir(parents=True, exist_ok=True)

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
            patch("builtins.input", return_value="y"),
            patch.object(acquire_mod, "slskd_search", return_value=[]),
            patch.object(acquire_mod, "ytdlp_download", side_effect=lambda *a, **kw: ytdlp_calls.append(a) or True),
        ):
            (tmp_path / "delta.json").write_text(json.dumps(sample_delta))
            acquire_mod.main()

        assert len(ytdlp_calls) == len(sample_delta["tracks"])
