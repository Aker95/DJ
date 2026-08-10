import json
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parents[2]


def load_env() -> dict:
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=True)
    return {
        "SPOTIFY_CLIENT_ID": os.getenv("SPOTIFY_CLIENT_ID"),
        "SPOTIFY_CLIENT_SECRET": os.getenv("SPOTIFY_CLIENT_SECRET"),
        "SPOTIFY_REDIRECT_URI": os.getenv("SPOTIFY_REDIRECT_URI"),
        "SPOTIFY_USER_ID": os.getenv("SPOTIFY_USER_ID"),
        "DISCOGS_TOKEN": os.getenv("DISCOGS_TOKEN"),
        "DISCOGS_USERNAME": os.getenv("DISCOGS_USERNAME"),
        "SLSKD_BASE_URL": os.getenv("SLSKD_BASE_URL", "http://localhost:5030"),
        "SLSKD_API_KEY": os.getenv("SLSKD_API_KEY"),
        "MUSIC_ROOT": os.getenv("MUSIC_ROOT"),
        "SCHEMA_PATH": os.getenv("SCHEMA_PATH"),
        "MIN_BITRATE_KBPS": int(os.getenv("MIN_BITRATE_KBPS", "320")),
        "PREFERRED_FORMATS": os.getenv("PREFERRED_FORMATS", "flac,wav,aiff,mp3").split(","),
        "YTDLP_OUTPUT_TEMPLATE": os.getenv("YTDLP_OUTPUT_TEMPLATE", "%(artist)s - %(title)s.%(ext)s"),
        "LASTFM_API_KEY": os.getenv("LASTFM_API_KEY"),
        "LASTFM_SHARED_SECRET": os.getenv("LASTFM_SHARED_SECRET"),
    }


def load_schema(path: Path = None) -> dict:
    if path is None:
        path = PROJECT_ROOT / "music_schema_knowledge.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_schema(schema: dict, path: Path = None) -> None:
    if path is None:
        path = PROJECT_ROOT / "music_schema_knowledge.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, ensure_ascii=False)
        f.write("\n")
