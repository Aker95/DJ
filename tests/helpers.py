from __future__ import annotations

import json
from pathlib import Path

from dj_library.config import Settings


def sample_schema() -> dict:
    return {
        "_meta": {"version": "3.0.0", "last_updated": "2026-08-19"},
        "main_genres": [
            {"id": "house", "name": "House", "aliases": [], "spotify_playlist": None},
            {"id": "trance", "name": "Trance", "aliases": [], "spotify_playlist": None},
        ],
        "master_folders": [
            {
                "id": "slow",
                "name": "01 • 118–125 WARM/GROOVE",
                "aliases": ["DJ CHILL / GROOVE"],
                "spotify_name_prefix": "01 • 118–125 WARM/GROOVE",
                "bpm_min": 118,
                "bpm_max": 126,
                "bpm_max_inclusive": False,
                "local_path": "music/slow",
                "genre_placements": [
                    {
                        "main_genre_id": "house",
                        "local_folder": "music/slow/house",
                        "spotify_playlist": "house-parent-slow",
                        "subgenres": [
                            {
                                "id": "deep-house",
                                "name": "Deep House",
                                "aliases": ["Deep"],
                                "local_folder": "music/slow/house/deep house",
                                "spotify_playlist": "deep-house-playlist",
                            }
                        ],
                    }
                ],
            },
            {
                "id": "fast",
                "name": "02 • 126–131 CLUB/DRIVE",
                "aliases": ["DJ PEAK HOUSE / CLUB"],
                "spotify_name_prefix": "02 • 126–131 CLUB/DRIVE",
                "bpm_min": 126,
                "bpm_max": 132,
                "bpm_max_inclusive": True,
                "local_path": "music/fast",
                "genre_placements": [
                    {
                        "main_genre_id": "trance",
                        "local_folder": "music/fast/trance",
                        "spotify_playlist": "trance-parent-fast",
                        "subgenres": [
                            {
                                "id": "uplifting-trance",
                                "name": "Uplifting Trance",
                                "aliases": [],
                                "local_folder": "music/fast/trance/uplifting trance",
                                "spotify_playlist": "uplifting-playlist",
                            }
                        ],
                    }
                ],
            },
        ],
    }


def settings_for(root: Path) -> Settings:
    schema_path = root / "music_schema_knowledge.json"
    schema_path.write_text(json.dumps(sample_schema()), encoding="utf-8")
    return Settings(
        project_root=root,
        schema_path=schema_path,
        data_root=root / "data",
        runs_root=root / "data/runs",
        music_root=root / "music",
        registry_path=root / "data/registry.json",
        spotify_cache_path=root / "data/spotify-token-cache.json",
        spotify_client_id="client",
        spotify_client_secret="secret",
        spotify_redirect_uri="http://localhost/callback",
        slskd_base_url="http://localhost:5030",
        slskd_api_key="key",
        slskd_downloads_dir=root / "slskd-downloads",
        slskd_container_name="slskd",
        min_bitrate_kbps=320,
        preferred_formats=("flac", "wav", "aiff", "mp3"),
    )
