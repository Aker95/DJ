from __future__ import annotations

import copy

import pytest
from tests.helpers import sample_schema, settings_for

from dj_library.schema import (
    apply_structure_manifest,
    bpm_master,
    build_structure_manifest,
    canonical_key,
    destination_index,
    iter_destinations,
    resolve_destination,
    slug,
    validate_schema,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Tiësto & Co.", "tiesto co"), ("  Deep-House ", "deep house"), ("", "")],
)
def test_canonical_key(value, expected):
    assert canonical_key(value) == expected


def test_slug():
    assert slug("Trance (Main Floor)") == "trance-main-floor"


@pytest.mark.parametrize(
    ("bpm", "master"),
    [(118, "slow"), (125.999, "slow"), (126, "fast"), (132, "fast"), (133, None)],
)
def test_bpm_boundaries(bpm, master):
    result = bpm_master(sample_schema(), bpm)
    assert (result or {}).get("id") == master


def test_destinations_include_parent_and_leaf():
    destinations = list(iter_destinations(sample_schema()))
    assert {item["classification_level"] for item in destinations} == {"main_genre", "subgenre"}
    assert {item["placement_id"] for item in destinations} == {
        "slow:house",
        "slow:house:deep-house",
        "fast:trance",
        "fast:trance:uplifting-trance",
    }


def test_alias_resolves_to_canonical_destination():
    result = resolve_destination(
        sample_schema(),
        master_folder="DJ CHILL / GROOVE",
        main_genre="House",
        subgenre="Deep",
    )
    assert result["placement_id"] == "slow:house:deep-house"


def test_destination_index_is_level_specific():
    index = destination_index(sample_schema())
    assert index[("01 118 125 warm groove", "main_genre", "house")]["subgenre"] is None


def test_validate_rejects_old_schema():
    schema = sample_schema()
    schema["_meta"]["version"] = "2.3.0"
    with pytest.raises(ValueError, match="Unsupported"):
        validate_schema(schema)


def test_validate_rejects_overlap():
    schema = sample_schema()
    schema["master_folders"][1]["bpm_min"] = 125
    with pytest.raises(ValueError, match="overlap"):
        validate_schema(schema)


def test_validate_rejects_alias_collision():
    schema = sample_schema()
    schema["main_genres"][1]["aliases"] = ["House"]
    with pytest.raises(ValueError, match="duplicates"):
        validate_schema(schema)


def test_validate_rejects_unknown_placement_genre():
    schema = sample_schema()
    schema["master_folders"][0]["genre_placements"][0]["main_genre_id"] = "missing"
    with pytest.raises(ValueError, match="unknown"):
        validate_schema(schema)


def test_structure_plan_and_apply_are_idempotent(tmp_path):
    settings = settings_for(tmp_path)
    schema = sample_schema()
    manifest = build_structure_manifest(schema, settings=settings)
    assert manifest["summary"]["folders_to_create"] == 4
    result = apply_structure_manifest(manifest, settings=settings)
    assert result["summary"]["created"] == 4
    assert build_structure_manifest(schema, settings=settings)["summary"]["folders_to_create"] == 0


def test_structure_apply_rejects_schema_change(tmp_path):
    settings = settings_for(tmp_path)
    manifest = build_structure_manifest(sample_schema(), settings=settings)
    settings.schema_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        apply_structure_manifest(manifest, settings=settings)


def test_schema_copy_does_not_mutate_fixture():
    schema = sample_schema()
    clone = copy.deepcopy(schema)
    validate_schema(clone)
    assert clone == schema
