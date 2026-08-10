"""Tests for the metadata-only, human web-research portion of module 06."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


SCRIPTS_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
spec = importlib.util.spec_from_file_location(
    "playlist_workflow_06_research", SCRIPTS_DIR / "06_playlist_workflow.py"
)
wf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wf)
sys.modules.setdefault("playlist_workflow_06_research", wf)


@pytest.fixture
def schema():
    return {
        "_meta": {"version": "1.0.0"},
        "master_folders": [
            {
                "id": "f1",
                "name": "DJ CHILL / GROOVE",
                "bpm_min": 120,
                "bpm_max": 126,
                "local_path": "music",
                "subgenres": [
                    {
                        "name": "Deep House",
                        "local_folder": "music/DJ CHILL - GROOVE/deep house",
                        "spotify_playlist": "DEEPHOUSE000000000001",
                        "notes": None,
                    }
                ],
            }
        ],
    }


def spotify_mock():
    sp = MagicMock()
    sp.current_user_playlists.return_value = {
        "items": [{"id": "SRC0000000000000000001", "name": "Research Queue"}],
        "next": None,
    }
    sp.playlist.return_value = {"name": "Research Queue"}
    sp.playlist_items.return_value = {
        "items": [
            {
                "item": {
                    "id": "TRACK00000000000000001",
                    "name": "Example (Extended Mix)",
                    "artists": [
                        {"id": "ARTIST0000000000000001", "name": "DJ Example"},
                        {"id": "ARTIST0000000000000002", "name": "Vocalist"},
                    ],
                    "album": {
                        "id": "ALBUM00000000000000001",
                        "name": "Example",
                        "release_date": "2026-01-02",
                    },
                    "duration_ms": 321000,
                    "external_ids": {"isrc": "USABC2600001"},
                    "external_urls": {
                        "spotify": "https://open.spotify.com/track/TRACK00000000000000001"
                    },
                }
            }
        ],
        "next": None,
    }
    return sp


def researched_artifact(schema):
    track = {
        "spotify_track_id": "TRACK00000000000000001",
        "artists": [{"spotify_artist_id": "A1", "name": "DJ Example"}],
        "artist": "DJ Example",
        "title": "Example (Extended Mix)",
        "album": "Example",
        "spotify_album_id": "ALBUM1",
        "isrc": "USABC2600001",
        "duration_ms": 321000,
        "release_date": "2026-01-02",
        "spotify_url": "https://open.spotify.com/track/TRACK00000000000000001",
        "subgenre": "Deep House",
        "master_folder": "DJ CHILL / GROOVE",
        "confidence": 0.9,
        "rationale": "The label identifies this exact extended mix as deep house.",
        "sources": [
            {
                "provider": "Example Records",
                "title": "Official release",
                "url": "https://example.com/releases/example",
            }
        ],
        "review_status": "recommended",
        "is_new_subgenre_proposal": False,
        "target_playlist_id": "DEEPHOUSE000000000001",
        "already_in_target_playlist": False,
    }
    return {
        "artifact_type": "spotify_genre_research",
        "artifact_version": 1,
        "status": "provisional",
        "source_playlist_id": "SRC0000000000000000001",
        "source_playlist_name": "Research Queue",
        "prepared_at": "2026-07-28T12:00:00+00:00",
        "schema_version": "1.0.0",
        "total_tracks": 1,
        "tracks": [track],
        "proposed_schema_additions": [],
        "summary": {
            "by_subgenre": {"Deep House": 1},
            "uncertain": 0,
            "proposed_schema_additions": 0,
            "already_in_correct_playlist": 0,
        },
    }


def test_resolves_spotify_url_without_playlist_lookup():
    sp = MagicMock()
    result = wf._resolve_playlist_id(
        sp, "https://open.spotify.com/playlist/1234567890123456789012?si=abc"
    )
    assert result == "1234567890123456789012"
    sp.current_user_playlists.assert_not_called()


def test_prepare_research_writes_rich_metadata_and_two_artifacts(tmp_path, schema):
    sp = spotify_mock()
    json_path = tmp_path / "research.json"
    markdown_path = tmp_path / "research.md"

    with patch.object(wf, "get_spotify_client", return_value=sp):
        result = wf.prepare_research(
            "Research Queue",
            env={},
            schema=schema,
            json_output_path=json_path,
            markdown_output_path=markdown_path,
        )

    assert result["status"] == "provisional"
    assert result["total_tracks"] == 1
    track = result["tracks"][0]
    assert track["artist"] == "DJ Example, Vocalist"
    assert track["isrc"] == "USABC2600001"
    assert track["album"] == "Example"
    assert track["duration_ms"] == 321000
    assert track["release_date"] == "2026-01-02"
    assert result["artifact_version"] == 2
    assert track["review_status"] == "pending"
    assert track["main_genre"] is None
    assert track["subgenre"] is None
    assert json.loads(json_path.read_text())["tracks"][0]["isrc"] == "USABC2600001"
    assert "# Genre Research: Research Queue" in markdown_path.read_text()


def test_prepare_research_does_not_import_or_call_external_classifier(tmp_path, schema):
    assert not hasattr(wf, "ConsensusClassifier")
    sp = spotify_mock()
    with patch.object(wf, "get_spotify_client", return_value=sp):
        wf.prepare_research(
            "Research Queue",
            env={},
            schema=schema,
            json_output_path=tmp_path / "research.json",
            markdown_output_path=tmp_path / "research.md",
        )


def test_validation_requires_citation_for_recommendation(schema):
    artifact = researched_artifact(schema)
    artifact["tracks"][0]["sources"] = []
    with pytest.raises(ValueError, match="direct HTTP"):
        wf.validate_research(artifact, schema)


def test_validation_rejects_forced_genre_on_uncertain_track(schema):
    artifact = researched_artifact(schema)
    artifact["tracks"][0]["review_status"] = "uncertain"
    with pytest.raises(ValueError, match="must not force"):
        wf.validate_research(artifact, schema)


def test_approve_research_creates_sort_contract_without_spotify_calls(
    tmp_path, schema
):
    artifact = researched_artifact(schema)
    output_path = tmp_path / "classify_results.json"
    with patch.object(wf, "get_spotify_client") as spotify_client:
        output = wf.approve_research(
            artifact,
            schema=schema,
            schema_path=tmp_path / "schema.json",
            output_path=output_path,
        )

    spotify_client.assert_not_called()
    assert output["classification_method"] == "human_web_research"
    assert output["tracks"][0]["subgenre"] == "Deep House"
    assert output["tracks"][0]["target_playlist_id"] == "DEEPHOUSE000000000001"
    assert output["tracks"][0]["is_broad_fallback"] is False
    assert json.loads(output_path.read_text())["total_tracks"] == 1


def test_approve_research_applies_declared_schema_proposal(tmp_path, schema):
    artifact = researched_artifact(schema)
    track = artifact["tracks"][0]
    track.update(
        {
            "subgenre": "Organic House",
            "master_folder": "DJ CHILL / GROOVE",
            "is_new_subgenre_proposal": True,
            "target_playlist_id": None,
        }
    )
    artifact["proposed_schema_additions"] = [
        {
            "name": "Organic House",
            "master_folder": "DJ CHILL / GROOVE",
            "bpm_justification": "The exact release is listed at 123 BPM.",
            "rationale": "The release is consistently identified as organic house.",
            "affected_track_ids": ["TRACK00000000000000001"],
        }
    ]
    artifact["summary"]["by_subgenre"] = {"Organic House": 1}
    artifact["summary"]["proposed_schema_additions"] = 1
    schema_path = tmp_path / "schema.json"

    output = wf.approve_research(
        artifact,
        schema=schema,
        schema_path=schema_path,
        output_path=tmp_path / "results.json",
    )

    saved_schema = json.loads(schema_path.read_text())
    added = saved_schema["master_folders"][0]["subgenres"][-1]
    assert added["name"] == "Organic House"
    assert added["spotify_playlist"] is None
    assert output["tracks"][0]["target_playlist_id"] is None


def test_refresh_summary_detects_track_already_in_target(schema):
    artifact = researched_artifact(schema)
    artifact["source_playlist_id"] = "DEEPHOUSE000000000001"
    wf.refresh_research_summary(artifact, schema)
    assert artifact["summary"]["already_in_correct_playlist"] == 1
    assert artifact["tracks"][0]["already_in_target_playlist"] is True
