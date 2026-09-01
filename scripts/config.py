"""Project paths and environment configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SUPPORTED_AUDIO_EXTENSIONS = frozenset(
    {".aac", ".aif", ".aiff", ".alac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}
)


@dataclass(frozen=True)
class Config:
    """Values shared by the active solo workflows."""

    project_root: Path
    data_dir: Path
    music_root: Path
    schema_path: Path
    spotify_cache: Path
    spotify_client_id: str | None
    spotify_client_secret: str | None
    spotify_redirect_uri: str | None
    discogs_token: str | None
    lastfm_api_key: str | None
    lastfm_shared_secret: str | None
    slskd_base_url: str
    slskd_api_key: str | None
    slskd_downloads_dir: Path
    min_bitrate_kbps: int
    preferred_formats: tuple[str, ...]
    rekordbox_database: Path

    def require_spotify(self) -> None:
        missing = [
            name
            for name, value in (
                ("SPOTIFY_CLIENT_ID", self.spotify_client_id),
                ("SPOTIFY_CLIENT_SECRET", self.spotify_client_secret),
                ("SPOTIFY_REDIRECT_URI", self.spotify_redirect_uri),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing Spotify configuration: {', '.join(missing)}")


def _path(value: str | None, default: Path) -> Path:
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def load_config() -> Config:
    """Load .env without overwriting variables already set by the shell."""

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    data_dir = PROJECT_ROOT / "data"
    preferred = tuple(
        item.strip().casefold()
        for item in os.getenv("PREFERRED_FORMATS", "flac,wav,aiff,mp3").split(",")
        if item.strip()
    )
    default_downloads = Path.home() / ".slskd" / "downloads"
    default_rekordbox = Path.home() / "Library/Pioneer/rekordbox/master.db"
    return Config(
        project_root=PROJECT_ROOT,
        data_dir=data_dir,
        music_root=_path(os.getenv("MUSIC_ROOT"), PROJECT_ROOT / "music"),
        schema_path=_path(
            os.getenv("SCHEMA_PATH"), PROJECT_ROOT / "music_schema_knowledge.json"
        ),
        spotify_cache=data_dir / "spotify-token-cache.json",
        spotify_client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        spotify_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        spotify_redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
        discogs_token=os.getenv("DISCOGS_TOKEN"),
        lastfm_api_key=os.getenv("LASTFM_API_KEY"),
        lastfm_shared_secret=os.getenv("LASTFM_SHARED_SECRET"),
        slskd_base_url=os.getenv("SLSKD_BASE_URL", "http://localhost:5030").rstrip("/"),
        slskd_api_key=os.getenv("SLSKD_API_KEY"),
        slskd_downloads_dir=_path(os.getenv("SLSKD_DOWNLOADS_DIR"), default_downloads),
        min_bitrate_kbps=int(os.getenv("MIN_BITRATE_KBPS", "320")),
        preferred_formats=preferred,
        rekordbox_database=_path(os.getenv("REKORDBOX_DATABASE"), default_rekordbox),
    )
