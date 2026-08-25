from __future__ import annotations

import pytest

from dj_library.config import Settings


def test_from_env_uses_project_relative_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("MUSIC_ROOT", raising=False)
    monkeypatch.setenv("PREFERRED_FORMATS", "flac, mp3")
    settings = Settings.from_env(tmp_path)
    assert settings.project_root == tmp_path.resolve()
    assert settings.music_root == (tmp_path / "music").resolve()
    assert settings.preferred_formats == ("flac", "mp3")
    assert settings.registry_path == tmp_path / "data/registry.json"


def test_require_spotify_reports_all_missing(tmp_path, monkeypatch):
    for key in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings.from_env(tmp_path)
    with pytest.raises(ValueError) as error:
        settings.require_spotify()
    assert "SPOTIFY_CLIENT_ID" in str(error.value)
    assert "SPOTIFY_REDIRECT_URI" in str(error.value)


def test_require_spotify_accepts_complete_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", "http://localhost")
    Settings.from_env(tmp_path).require_spotify()
