"""Module 06 v2 research, approval, and sorting contracts."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


SCRIPTS_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
spec = importlib.util.spec_from_file_location(
    "playlist_workflow_06_hierarchy", SCRIPTS_DIR / "06_playlist_workflow.py"
)
wf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wf)


@pytest.fixture
def schema_v2():
    return {
        "_meta": {"version": "2.0.0", "last_updated": "2026-08-07"},
        "main_genres": [
            {
                "id": "trance",
                "name": "Trance",
                "aliases": [],
                "spotify_playlist": "PARENT_PLAYLIST",
                "notes": None,
            }
        ],
        "master_folders": [
            {
                "id": "hard",
                "name": "DJ HARD / NEO-RAVE",
                "local_path": "music/DJ HARD - NEO-RAVE",
                "genre_placements": [
                    {
                        "main_genre_id": "trance",
                        "local_folder": "music/DJ HARD - NEO-RAVE/trance",
                        "migration_status": "complete",
                        "subgenres": [
                            {
                                "id": "hard-trance",
                                "name": "Hard Trance",
                                "aliases": [],
                                "local_folder": "music/DJ HARD - NEO-RAVE/trance/hard-trance",
                                "migration_status": "complete",
                                "spotify_playlist": "LEAF_PLAYLIST",
                                "notes": None,
                            }
                        ],
                    }
                ],
            }
        ],
    }


def artifact(*, subgenre="Hard Trance", confidence=0.9, evidence_scope="track"):
    track = {
        "spotify_track_id": "TRACK1",
        "artists": [{"spotify_artist_id": "ARTIST1", "name": "Example"}],
        "artist": "Example",
        "title": "Test Mix",
        "album": "Test",
        "spotify_album_id": "ALBUM1",
        "isrc": "XXABC2600001",
        "duration_ms": 300000,
        "release_date": "2026-01-01",
        "spotify_url": "https://open.spotify.com/track/TRACK1",
        "main_genre": "Trance",
        "subgenre": subgenre,
        "classification_level": "subgenre" if subgenre else "main_genre",
        "master_folder": "DJ HARD / NEO-RAVE",
        "confidence": confidence,
        "rationale": "The release and tempo support this classification.",
        "evidence": [
            {
                "provider": "Label",
                "title": "Release page",
                "url": "https://example.test/release",
                "scope": evidence_scope,
            }
        ],
        "review_flags": [] if evidence_scope in {"track", "release"} else ["artist-only"],
        "review_status": "proposed",
        "is_new_main_genre_proposal": False,
        "is_new_subgenre_proposal": False,
        "target_playlist_id": None,
        "target_kind": "subgenre" if subgenre else "main_genre",
        "already_in_target_playlist": False,
    }
    return {
        "artifact_type": "spotify_genre_research",
        "artifact_version": 2,
        "status": "provisional",
        "source_playlist_id": "SOURCE_PLAYLIST",
        "source_playlist_name": "Research Queue",
        "prepared_at": "2026-08-07T12:00:00+00:00",
        "schema_version": "2.0.0",
        "total_tracks": 1,
        "tracks": [track],
        "proposed_main_genres": [],
        "proposed_subgenres": [],
        "proposed_aliases": [],
        "proposed_routing_changes": [],
        "summary": {},
    }


def test_parent_fallback_is_valid_and_maps_to_parent_playlist(schema_v2):
    research = artifact(subgenre=None)
    wf.refresh_research_summary(research, schema_v2)
    wf.validate_research(research, schema_v2)
    assert research["tracks"][0]["target_playlist_id"] == "PARENT_PLAYLIST"
    assert research["summary"]["parent_fallbacks"] == 1


def test_artist_only_evidence_is_valid_when_flagged_and_not_overconfident(schema_v2):
    research = artifact(confidence=0.7, evidence_scope="artist")
    wf.validate_research(research, schema_v2)


def test_artist_only_evidence_cannot_claim_high_confidence(schema_v2):
    research = artifact(confidence=0.9, evidence_scope="artist")
    with pytest.raises(ValueError, match="track- or release-scoped"):
        wf.validate_research(research, schema_v2)


def test_every_valid_track_requires_a_main_genre(schema_v2):
    research = artifact()
    research["tracks"][0]["main_genre"] = None
    with pytest.raises(ValueError, match="every valid track requires main_genre"):
        wf.validate_research(research, schema_v2)


def test_new_subgenre_is_merged_and_review_history_is_immutable(tmp_path, schema_v2):
    research = artifact(subgenre="Main Floor Trance")
    track = research["tracks"][0]
    track["is_new_subgenre_proposal"] = True
    track["review_flags"] = ["new-taxonomy"]
    research["proposed_subgenres"] = [
        {
            "id": "main-floor-trance",
            "name": "Main Floor Trance",
            "aliases": ["Trance (Main Floor)"],
            "main_genre": "Trance",
            "master_folder": "DJ HARD / NEO-RAVE",
            "bpm_justification": "The exact release is listed at 150 BPM.",
            "rationale": "Stores consistently use the main-floor trance style.",
            "affected_track_ids": ["TRACK1"],
        }
    ]
    schema_path = tmp_path / "schema.json"
    output_path = tmp_path / "classify_results.json"

    with patch.object(wf, "get_spotify_client") as spotify:
        output = wf.approve_research(
            research,
            schema=schema_v2,
            schema_path=schema_path,
            output_path=output_path,
        )

    spotify.assert_not_called()
    assert output["artifact_version"] == 2
    assert output["tracks"][0]["approved"] is True
    assert output["tracks"][0]["target_playlist_id"] is None
    saved = json.loads(schema_path.read_text())
    added = saved["master_folders"][0]["genre_placements"][0]["subgenres"][-1]
    assert added["name"] == "Main Floor Trance"
    assert added["aliases"] == ["Trance (Main Floor)"]
    history = tmp_path / output["review_history"]
    assert (history / "provisional.json").exists()
    assert (history / "reviewed.json").exists()
    assert (history / "approval_manifest.json").exists()
    with pytest.raises(FileExistsError, match="Immutable review history"):
        wf.approve_research(
            research,
            schema=schema_v2,
            schema_path=schema_path,
            output_path=output_path,
        )


def test_v2_sort_honors_approval_over_confidence_and_parent_routing(schema_v2):
    results = {
        "artifact_version": 2,
        "source_playlist_id": "SOURCE",
        "source_playlist_name": "Queue",
        "tracks": [
            {
                "spotify_track_id": "TRACK1",
                "artist": "Example",
                "title": "Test Mix",
                "approved": True,
                "main_genre": "Trance",
                "subgenre": None,
                "confidence": 0.1,
                "target_playlist_id": "PARENT_PLAYLIST",
            }
        ],
    }
    with patch.object(wf, "get_spotify_client") as spotify:
        summary = wf.sort_tracks(
            results,
            env={},
            schema=schema_v2,
            min_confidence=0.99,
            dry_run=True,
        )
    spotify.assert_not_called()
    assert summary["moved"] == 1
    assert summary["skipped"]["low_confidence"] == 0

