"""Contract tests for the shared hierarchical taxonomy resolver."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from utils.schema import (  # noqa: E402
    iter_destinations,
    resolve_destination,
    validate_schema,
)


def schema_v2():
    return {
        "_meta": {"version": "2.0.0"},
        "main_genres": [
            {
                "id": "trance",
                "name": "Trance",
                "aliases": ["Main Trance"],
                "spotify_playlist": "PARENT",
            }
        ],
        "master_folders": [
            {
                "id": "fast",
                "name": "DJ FAST",
                "local_path": "music/DJ FAST",
                "genre_placements": [
                    {
                        "main_genre_id": "trance",
                        "local_folder": "music/DJ FAST/trance",
                        "subgenres": [
                            {
                                "id": "hard-trance",
                                "name": "Hard Trance",
                                "aliases": ["Hardtrance"],
                                "local_folder": "music/DJ FAST/trance/hard-trance",
                                "spotify_playlist": "LEAF",
                            }
                        ],
                    }
                ],
            },
            {
                "id": "faster",
                "name": "DJ FASTER",
                "local_path": "music/DJ FASTER",
                "genre_placements": [
                    {
                        "main_genre_id": "trance",
                        "local_folder": "music/DJ FASTER/trance",
                        "subgenres": [],
                    }
                ],
            },
        ],
    }


def test_global_parent_is_reused_across_master_folders():
    schema = schema_v2()
    validate_schema(schema)
    parents = [
        item for item in iter_destinations(schema)
        if item["classification_level"] == "main_genre"
    ]
    assert [item["master_folder"] for item in parents] == ["DJ FAST", "DJ FASTER"]
    assert {item["spotify_playlist"] for item in parents} == {"PARENT"}


def test_aliases_resolve_to_one_canonical_destination():
    destination = resolve_destination(
        schema_v2(),
        master_folder="DJ FAST",
        main_genre="Main Trance",
        subgenre="Hardtrance",
    )
    assert destination["name"] == "Hard Trance"
    assert destination["spotify_playlist"] == "LEAF"


def test_synonym_collision_is_rejected_globally():
    schema = schema_v2()
    schema["main_genres"].append(
        {
            "id": "trance-duplicate",
            "name": "Electronic",
            "aliases": ["Hardtrance"],
            "spotify_playlist": None,
        }
    )
    with pytest.raises(ValueError, match="duplicates"):
        validate_schema(schema)


def test_v1_leaf_traversal_remains_supported():
    schema = {
        "_meta": {"version": "1.0.0"},
        "master_folders": [
            {
                "name": "Legacy",
                "subgenres": [
                    {
                        "name": "Deep House",
                        "local_folder": "music/legacy/deep-house",
                        "spotify_playlist": "LEGACY",
                    }
                ],
            }
        ],
    }
    destinations = list(iter_destinations(schema))
    assert len(destinations) == 1
    assert destinations[0]["name"] == "Deep House"

