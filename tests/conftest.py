"""
Shared pytest fixtures for the DJ Music Pipeline test suite.
"""

import json
import pytest
from pathlib import Path


@pytest.fixture
def sample_schema():
    return {
        "_meta": {
            "version": "1.0.0",
            "last_updated": "2026-06-24",
            "description": "Test schema",
        },
        "master_folders": [
            {
                "id": "folder_1",
                "name": "DJ CHILL / GROOVE",
                "bpm_min": 120,
                "bpm_max": 126,
                "local_path": "music",
                "subgenres": [
                    {
                        "name": "Afro House",
                        "local_folder": "music/afro house",
                        "spotify_playlist": None,
                        "notes": None,
                    },
                    {
                        "name": "Deep House",
                        "local_folder": "music/deep house",
                        "spotify_playlist": "existing_pl_id",
                        "notes": None,
                    },
                    {
                        "name": "Melodic House",
                        "local_folder": None,
                        "spotify_playlist": None,
                        "notes": "No local folder yet",
                    },
                ],
            },
            {
                "id": "folder_2",
                "name": "DJ PEAK HOUSE / CLUB",
                "bpm_min": 126,
                "bpm_max": 132,
                "local_path": "music",
                "subgenres": [
                    {
                        "name": "Tech House",
                        "local_folder": "music/tech house",
                        "spotify_playlist": None,
                        "notes": None,
                    },
                    {
                        "name": "Bass House",
                        "local_folder": "music/bass house",
                        "spotify_playlist": None,
                        "notes": None,
                    },
                ],
            },
        ],
    }


@pytest.fixture
def sample_snapshot():
    return {
        "generated_at": "2026-06-24T12:00:00+00:00",
        "total_playlists": 2,
        "playlists": [
            {
                "playlist_id": "pl_afro_001",
                "playlist_name": "Afro House",
                "schema_subgenre": "Afro House",
                "master_folder": "DJ CHILL / GROOVE",
                "local_folder": "music/afro house",
                "tracks": [
                    {"id": "t1", "title": "Kawa", "artist": "Black Coffee", "duration_ms": 360000},
                    {"id": "t2", "title": "Superman", "artist": "Enoo Napa", "duration_ms": 420000},
                    {"id": "t3", "title": "Missing Track", "artist": "Artist B", "duration_ms": 300000},
                ],
            },
            {
                "playlist_id": "pl_tech_002",
                "playlist_name": "Tech House",
                "schema_subgenre": "Tech House",
                "master_folder": "DJ PEAK HOUSE / CLUB",
                "local_folder": "music/tech house",
                "tracks": [
                    {"id": "t4", "title": "Love Story", "artist": "Victor Ruiz", "duration_ms": 390000},
                    {"id": "t5", "title": "Deeper", "artist": "DJ C", "duration_ms": 400000},
                ],
            },
        ],
    }


@pytest.fixture
def sample_delta():
    return {
        "generated_at": "2026-06-24T12:00:00+00:00",
        "total_missing": 3,
        "tracks": [
            {
                "artist": "Artist B",
                "title": "Missing Track",
                "spotify_id": "t3",
                "subgenre": "Afro House",
                "master_folder": "DJ CHILL / GROOVE",
                "local_folder": "music/afro house",
            },
            {
                "artist": "Victor Ruiz",
                "title": "Love Story",
                "spotify_id": "t4",
                "subgenre": "Tech House",
                "master_folder": "DJ PEAK HOUSE / CLUB",
                "local_folder": "music/tech house",
            },
            {
                "artist": "Vintage Culture",
                "title": "Lost",
                "spotify_id": "t6",
                "subgenre": "Deep House",
                "master_folder": "DJ CHILL / GROOVE",
                "local_folder": "music/deep house",
            },
        ],
    }


@pytest.fixture
def schema_file(tmp_path, sample_schema):
    """Writes sample_schema to a temp file and returns its path."""
    p = tmp_path / "music_schema_knowledge.json"
    p.write_text(json.dumps(sample_schema, indent=2))
    return p


@pytest.fixture
def snapshot_file(tmp_path, sample_snapshot):
    """Writes sample_snapshot to a temp file and returns its path."""
    p = tmp_path / "spotify_snapshot.json"
    p.write_text(json.dumps(sample_snapshot, indent=2))
    return p


@pytest.fixture
def delta_file(tmp_path, sample_delta):
    """Writes sample_delta to a temp file and returns its path."""
    p = tmp_path / "delta.json"
    p.write_text(json.dumps(sample_delta, indent=2))
    return p


@pytest.fixture
def music_root(tmp_path):
    """Creates a music/ directory tree mirroring real structure."""
    root = tmp_path / "music"
    root.mkdir()
    return root
