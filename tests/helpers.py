from pathlib import Path

from utils.config import Config


def sample_schema() -> dict:
    return {
        "_meta": {"version": "3.0.0"},
        "main_genres": [{"id": "techno", "name": "Techno", "aliases": []}],
        "master_folders": [
            {
                "id": "fast",
                "name": "FAST",
                "aliases": [],
                "spotify_name_prefix": "FAST",
                "bpm_min": 132,
                "bpm_max": 145,
                "bpm_max_inclusive": False,
                "local_path": "music/fast",
                "genre_placements": [
                    {
                        "main_genre_id": "techno",
                        "local_folder": "music/fast/techno",
                        "spotify_playlist": "parent-playlist",
                        "subgenres": [
                            {
                                "id": "hard-groove",
                                "name": "Hard Groove",
                                "aliases": ["Hardgroove"],
                                "local_folder": "music/fast/techno/hard groove",
                                "spotify_playlist": "leaf-playlist",
                                "notes": None,
                            }
                        ],
                    }
                ],
            }
        ],
    }


def config_for(root: Path) -> Config:
    return Config(
        project_root=root,
        data_dir=root / "data",
        music_root=root / "music",
        schema_path=root / "music_schema_knowledge.json",
        spotify_cache=root / "data/token.json",
        spotify_client_id="id",
        spotify_client_secret="secret",
        spotify_redirect_uri="http://localhost",
        discogs_token=None,
        lastfm_api_key=None,
        lastfm_shared_secret=None,
        slskd_base_url="http://localhost:5030",
        slskd_api_key="key",
        slskd_downloads_dir=root / "downloads",
        min_bitrate_kbps=320,
        preferred_formats=("flac", "mp3"),
        rekordbox_database=root / "master.db",
    )
