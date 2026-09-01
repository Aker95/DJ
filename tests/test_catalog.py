from copy import deepcopy

import pytest
from catalog import (
    _apply_catalog_changes,
    _playlist_name,
    list_destinations,
    resolve_destination,
    set_spotify_playlist,
    validate_catalog,
)

from tests.helpers import sample_schema


def test_playlist_name_keeps_full_taxonomy_when_within_spotify_limit():
    master = {
        "name": "03 • 132–144 FAST/RAVE",
        "spotify_name_prefix": "03 • 132–144 FAST/RAVE",
    }
    genre = {"name": "Techno"}
    subgenre = {"name": "Hard Groove"}

    assert (
        _playlist_name(master, genre, subgenre)
        == "03 • 132–144 FAST/RAVE • Techno • Hard Groove"
    )


def test_playlist_name_drops_main_genre_when_full_name_exceeds_spotify_limit():
    master = {
        "name": "03 • 132–144 FAST/RAVE",
        "spotify_name_prefix": "03 • 132–144 FAST/RAVE",
    }
    genre = {"name": "T" * 75}
    subgenre = {"name": "Hard Groove"}

    name = _playlist_name(master, genre, subgenre)

    assert name == "03 • 132–144 FAST/RAVE • Hard Groove"
    assert len(name) <= 100


def test_resolve_destination_accepts_alias():
    destination = resolve_destination(sample_schema(), "FAST", "Techno", "Hardgroove")
    assert destination["placement_id"] == "fast:techno:hard-groove"


def test_schema_changes_are_idempotent_and_reuse_subgenre_identity():
    schema = sample_schema()
    schema["master_folders"].append(
        {
            "id": "hard",
            "name": "HARD",
            "aliases": [],
            "bpm_min": 145,
            "bpm_max": 180,
            "bpm_max_inclusive": True,
            "local_path": "music/hard",
            "genre_placements": [],
        }
    )
    changes = [
        {
            "action": "ensure_subgenre",
            "name": "Hard Groove",
            "main_genre": "Techno",
            "master_folder": "HARD",
        }
    ]
    updated, first = _apply_catalog_changes(schema, changes)
    updated_again, second = _apply_catalog_changes(updated, changes)
    assert first[0]["placement_added"] is True
    assert second[0]["placement_added"] is False
    assert (
        resolve_destination(updated_again, "HARD", "Techno", "Hard Groove")[
            "placement_id"
        ]
        == "hard:techno:hard-groove"
    )


def test_alias_change_does_not_duplicate_alias():
    change = {
        "action": "add_alias",
        "canonical_name": "Techno",
        "alias": "Techno Music",
    }
    updated, _ = _apply_catalog_changes(sample_schema(), [change, change])
    assert updated["main_genres"][0]["aliases"] == ["Techno Music"]
    validate_catalog(deepcopy(updated))


def test_catalog_rejects_a_placement_for_an_unknown_main_genre():
    catalog = sample_schema()
    catalog["master_folders"][0]["genre_placements"][0]["main_genre_id"] = "missing"
    with pytest.raises(ValueError, match="unknown main genre"):
        validate_catalog(catalog)


def test_destinations_do_not_leak_nodes_and_mapping_uses_catalog_operation():
    catalog = sample_schema()
    destination = resolve_destination(catalog, "FAST", "Techno", "Hard Groove")
    assert "node" not in destination
    updated = set_spotify_playlist(catalog, destination["placement_id"], "replacement")
    assert list_destinations(updated)[1]["spotify_playlist"] == "replacement"
    assert list_destinations(catalog)[1]["spotify_playlist"] == "leaf-playlist"
