from __future__ import annotations

import copy

import pytest
from tests.helpers import sample_schema

from dj_library.research import (
    apply_decisions,
    approve_research,
    prepare_research,
    render_markdown,
    validate_research,
)


class ResearchSpotify:
    def playlist(self, playlist_id, fields=None):
        return {"id": playlist_id, "name": "Research Queue"}

    def playlist_items(self, _playlist_id, **_kwargs):
        return {
            "items": [
                {
                    "item": {
                        "id": "a" * 22,
                        "name": "Ocean",
                        "artists": [{"id": "artist", "name": "Example"}],
                        "album": {"id": "album", "name": "Release", "release_date": "2025"},
                        "duration_ms": 180000,
                        "external_ids": {"isrc": "XX123"},
                        "external_urls": {"spotify": "https://spotify.test/track"},
                    }
                }
            ],
            "next": None,
        }

    def next(self, _page):
        return None


def valid_decision() -> dict:
    return {
        "tracks": [
            {
                "spotify_track_id": "a" * 22,
                "main_genre": "House",
                "subgenre": "Deep House",
                "master_folder": "01 • 118–125 WARM/GROOVE",
                "metadata_bpm": 124,
                "metadata_bpm_sources": ["https://example.test/bpm"],
                "bpm_basis": "metadata",
                "confidence": 0.9,
                "rationale": "Exact release and store taxonomy agree.",
                "evidence": [{"url": "https://example.test/release"}],
                "review_flags": [],
            }
        ]
    }


def test_prepare_decide_validate_approve():
    schema = sample_schema()
    prepared = prepare_research("a" * 22, spotify=ResearchSpotify(), schema=schema)
    reviewed = apply_decisions(prepared, valid_decision())
    assert validate_research(reviewed, schema)["tracks"] == 1
    approved, unchanged_schema, audit = approve_research(reviewed, schema)
    assert approved["tracks"][0]["placement_id"] == "slow:house:deep-house"
    assert unchanged_schema == schema
    assert audit["manifest_sha256"]
    assert "Example — Ocean" in render_markdown(reviewed)


def test_decisions_reject_unknown_track():
    prepared = prepare_research("a" * 22, spotify=ResearchSpotify(), schema=sample_schema())
    with pytest.raises(ValueError, match="unknown"):
        apply_decisions(prepared, {"tracks": [{"spotify_track_id": "missing"}]})


def test_validation_combines_evidence_errors():
    prepared = prepare_research("a" * 22, spotify=ResearchSpotify(), schema=sample_schema())
    invalid = copy.deepcopy(valid_decision())
    invalid["tracks"][0].update({"confidence": 2, "rationale": "", "evidence": []})
    reviewed = apply_decisions(prepared, invalid)
    with pytest.raises(ValueError) as error:
        validate_research(reviewed, sample_schema())
    assert "confidence" in str(error.value)
    assert "evidence" in str(error.value)


def test_approval_can_add_a_proposed_subgenre():
    schema = sample_schema()
    prepared = prepare_research("a" * 22, spotify=ResearchSpotify(), schema=schema)
    decisions = valid_decision()
    decisions["tracks"][0]["subgenre"] = "Dream House"
    decisions["proposed_subgenres"] = [
        {
            "name": "Dream House",
            "master_folder": "01 • 118–125 WARM/GROOVE",
            "main_genre": "House",
            "rationale": "A stable leaf for this sound.",
        }
    ]
    approved, updated, _audit = approve_research(prepared, schema, decisions=decisions)
    assert approved["tracks"][0]["subgenre"] == "Dream House"
    assert updated is not schema
