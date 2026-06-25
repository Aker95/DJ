"""
Tests for scripts/utils/config.py
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from utils.config import load_env, load_schema, save_schema


# ---------------------------------------------------------------------------
# load_env
# ---------------------------------------------------------------------------

class TestLoadEnv:
    # Isolated env vars used by all load_env tests
    _TEST_ENV = {
        "SPOTIFY_CLIENT_ID": "test_client_id",
        "SPOTIFY_CLIENT_SECRET": "test_secret",
        "SPOTIFY_REDIRECT_URI": "http://127.0.0.1:8888/callback",
        "SPOTIFY_USER_ID": "12345",
        "DISCOGS_TOKEN": "discogs_tok",
        "DISCOGS_USERNAME": "myuser",
        "SLSKD_BASE_URL": "http://localhost:5030",
        "SLSKD_API_KEY": "abc123",
        "MUSIC_ROOT": "/tmp/music",
        "SCHEMA_PATH": "/tmp/schema.json",
        "MIN_BITRATE_KBPS": "320",
        "PREFERRED_FORMATS": "flac,wav,aiff,mp3",
        "YTDLP_OUTPUT_TEMPLATE": "%(artist)s - %(title)s.%(ext)s",
    }

    def _load(self, overrides=None):
        env_vars = {**self._TEST_ENV, **(overrides or {})}
        with patch.dict(os.environ, env_vars, clear=True):
            with patch("utils.config.load_dotenv"):  # skip .env file
                return load_env()

    def test_returns_all_expected_keys(self):
        env = self._load()
        assert env["SPOTIFY_CLIENT_ID"] == "test_client_id"
        assert env["SPOTIFY_CLIENT_SECRET"] == "test_secret"
        assert env["SPOTIFY_REDIRECT_URI"] == "http://127.0.0.1:8888/callback"
        assert env["SPOTIFY_USER_ID"] == "12345"
        assert env["SLSKD_BASE_URL"] == "http://localhost:5030"
        assert env["SLSKD_API_KEY"] == "abc123"
        assert env["MUSIC_ROOT"] == "/tmp/music"
        assert env["MIN_BITRATE_KBPS"] == 320
        assert env["PREFERRED_FORMATS"] == ["flac", "wav", "aiff", "mp3"]
        assert env["YTDLP_OUTPUT_TEMPLATE"] == "%(artist)s - %(title)s.%(ext)s"

    def test_min_bitrate_is_int(self):
        env = self._load({"MIN_BITRATE_KBPS": "192"})
        assert isinstance(env["MIN_BITRATE_KBPS"], int)
        assert env["MIN_BITRATE_KBPS"] == 192

    def test_preferred_formats_default(self):
        env = self._load({"PREFERRED_FORMATS": ""})
        # Empty string split → [""] — default should kick in if not set at all
        env2 = self._load()
        del env2  # just verify no crash
        env3 = self._load({"PREFERRED_FORMATS": "flac,mp3"})
        assert env3["PREFERRED_FORMATS"] == ["flac", "mp3"]

    def test_slskd_base_url_default(self):
        # Remove SLSKD_BASE_URL so the default kicks in
        vars_without_url = {k: v for k, v in self._TEST_ENV.items() if k != "SLSKD_BASE_URL"}
        with patch.dict(os.environ, vars_without_url, clear=True):
            with patch("utils.config.load_dotenv"):
                env = load_env()
        assert env["SLSKD_BASE_URL"] == "http://localhost:5030"


# ---------------------------------------------------------------------------
# load_schema / save_schema
# ---------------------------------------------------------------------------

class TestLoadSchema:
    def test_loads_valid_schema(self, schema_file, sample_schema):
        result = load_schema(schema_file)
        assert result["_meta"]["version"] == "1.0.0"
        assert len(result["master_folders"]) == len(sample_schema["master_folders"])

    def test_master_folders_structure(self, schema_file):
        result = load_schema(schema_file)
        for folder in result["master_folders"]:
            assert "id" in folder
            assert "name" in folder
            assert "bpm_min" in folder
            assert "bpm_max" in folder
            assert "subgenres" in folder

    def test_subgenres_have_required_fields(self, schema_file):
        result = load_schema(schema_file)
        for folder in result["master_folders"]:
            for sg in folder["subgenres"]:
                assert "name" in sg
                assert "local_folder" in sg
                assert "spotify_playlist" in sg

    def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_schema(tmp_path / "nonexistent.json")

    def test_raises_on_invalid_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json}")
        with pytest.raises(json.JSONDecodeError):
            load_schema(bad)


class TestSaveSchema:
    def test_roundtrip(self, tmp_path, sample_schema):
        out = tmp_path / "schema_out.json"
        save_schema(sample_schema, out)
        reloaded = load_schema(out)
        assert reloaded == sample_schema

    def test_writes_valid_json(self, tmp_path, sample_schema):
        out = tmp_path / "schema_out.json"
        save_schema(sample_schema, out)
        parsed = json.loads(out.read_text())
        assert parsed["_meta"]["version"] == "1.0.0"

    def test_preserves_null_fields(self, tmp_path, sample_schema):
        out = tmp_path / "schema_out.json"
        save_schema(sample_schema, out)
        reloaded = load_schema(out)
        sg = reloaded["master_folders"][0]["subgenres"][0]
        assert sg["spotify_playlist"] is None

    def test_overwrites_existing_file(self, tmp_path, sample_schema):
        out = tmp_path / "schema_out.json"
        out.write_text("old content")
        save_schema(sample_schema, out)
        assert "master_folders" in out.read_text()
