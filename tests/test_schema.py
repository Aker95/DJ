from copy import deepcopy

from tests.helpers import sample_schema
from utils.schema import apply_schema_changes, resolve_destination, validate_schema


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
        {"action": "ensure_subgenre", "name": "Hard Groove", "main_genre": "Techno", "master_folder": "HARD"}
    ]
    updated, first = apply_schema_changes(schema, changes)
    updated_again, second = apply_schema_changes(updated, changes)
    assert first[0]["placement_added"] is True
    assert second[0]["placement_added"] is False
    assert resolve_destination(updated_again, "HARD", "Techno", "Hard Groove")["placement_id"] == "hard:techno:hard-groove"


def test_alias_change_does_not_duplicate_alias():
    change = {"action": "add_alias", "canonical_name": "Techno", "alias": "Techno Music"}
    updated, _ = apply_schema_changes(sample_schema(), [change, change])
    assert updated["main_genres"][0]["aliases"] == ["Techno Music"]
    validate_schema(deepcopy(updated))
