from pathlib import Path

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from .config import load_env, PROJECT_ROOT

CACHE_PATH = PROJECT_ROOT / ".spotify_cache"


def clear_token_cache() -> None:
    """Delete the cached OAuth token file."""
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()


def get_spotify_client(scope: str = "playlist-read-private") -> spotipy.Spotify:
    env = load_env()
    auth_manager = SpotifyOAuth(
        client_id=env["SPOTIFY_CLIENT_ID"],
        client_secret=env["SPOTIFY_CLIENT_SECRET"],
        redirect_uri=env["SPOTIFY_REDIRECT_URI"],
        scope=scope,
        cache_path=str(CACHE_PATH),
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth_manager)
