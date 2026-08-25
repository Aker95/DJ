"""Project paths and environment-backed runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """All mutable environment and filesystem boundaries for a workflow run."""

    project_root: Path
    schema_path: Path
    data_root: Path
    runs_root: Path
    music_root: Path
    registry_path: Path
    spotify_cache_path: Path
    spotify_client_id: str | None
    spotify_client_secret: str | None
    spotify_redirect_uri: str | None
    slskd_base_url: str
    slskd_api_key: str | None
    slskd_downloads_dir: Path
    slskd_container_name: str
    min_bitrate_kbps: int
    preferred_formats: tuple[str, ...]

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> Settings:
        """Load settings without changing process-global paths or prompting."""

        root = (project_root or Path.cwd()).resolve()
        load_dotenv(root / ".env", override=False)
        data_root = root / "data"
        preferred = tuple(
            item.strip().casefold()
            for item in os.getenv("PREFERRED_FORMATS", "flac,wav,aiff,mp3").split(",")
            if item.strip()
        )
        return cls(
            project_root=root,
            schema_path=root / "music_schema_knowledge.json",
            data_root=data_root,
            runs_root=data_root / "runs",
            music_root=Path(os.getenv("MUSIC_ROOT", root / "music")).expanduser().resolve(),
            registry_path=data_root / "registry.json",
            spotify_cache_path=data_root / "spotify-token-cache.json",
            spotify_client_id=os.getenv("SPOTIFY_CLIENT_ID"),
            spotify_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
            spotify_redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
            slskd_base_url=os.getenv("SLSKD_BASE_URL", "http://localhost:5030").rstrip("/"),
            slskd_api_key=os.getenv("SLSKD_API_KEY"),
            slskd_downloads_dir=Path(
                os.getenv("SLSKD_DOWNLOADS_DIR", Path.home() / ".slskd/downloads")
            )
            .expanduser()
            .resolve(),
            slskd_container_name=os.getenv("SLSKD_CONTAINER_NAME", "slskd"),
            min_bitrate_kbps=int(os.getenv("MIN_BITRATE_KBPS", "320")),
            preferred_formats=preferred,
        )

    def require_spotify(self) -> None:
        """Fail with one actionable error when Spotify credentials are incomplete."""

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
