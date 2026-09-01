from pathlib import Path

import pytest
from classification import (
    _prepare_document,
    apply_decisions,
    approve_research,
    render_research,
)

from tests.helpers import sample_schema


def researched_document():
    document = _prepare_document(
        {"id": "source-playlist", "name": "Research Bag"},
        [
            {
                "spotify_track_id": "track-1",
                "artist": "Artist",
                "title": "Track",
                "album": "Release",
                "release_date": "2026",
                "isrc": "CODE",
                "duration_ms": 300000,
                "spotify_url": "https://open.spotify.com/track/track-1",
                "provider_evidence": {
                    "discogs": {
                        "styles": ["Hard Groove"],
                        "url": "https://discogs.com/release/1",
                        "warning": None,
                    },
                    "lastfm": {
                        "tags": ["techno"],
                        "url": "https://last.fm/track",
                        "warning": None,
                    },
                },
            }
        ],
        sample_schema(),
    )
    document["tracks"][0].update(
        {
            "master_folder": "FAST",
            "main_genre": "Techno",
            "subgenre": "Hard Groove",
            "rationale": "Exact release styles and label context support hard groove.",
            "alternatives_considered": ["Techno parent"],
            "research_status": "reviewed",
        }
    )
    return document


def test_research_markdown_is_a_playlist_breakdown():
    markdown = render_research(researched_document())
    assert "# Playlist Research: Research Bag" in markdown
    assert "### Main genres" in markdown
    assert "Artist — Track" in markdown
    assert "Alternatives considered: Techno parent" in markdown
    assert "https://discogs.com/release/1" in markdown


def test_research_counts_unavailable_playlist_items():
    document = _prepare_document(
        {"id": "source", "name": "Bag", "total_items": 3},
        researched_document()["tracks"],
        sample_schema(),
    )
    assert document["summary"]["available_tracks"] == 1
    assert document["summary"]["unavailable_tracks"] == 2


def test_approval_derives_canonical_destination():
    classifications, reviewed = approve_research(
        researched_document(), None, sample_schema()
    )
    assert classifications["tracks"][0]["placement_id"] == "fast:techno:hard-groove"
    assert reviewed["summary"]["pending"] == 0
    assert reviewed["tracks"][0]["research_status"] == "approved"


def test_user_correction_is_applied_before_approval():
    document = researched_document()
    document["tracks"][0]["subgenre"] = None
    decisions = {
        "tracks": {
            "track-1": {
                "subgenre": "Hard Groove",
                "rationale": "User-confirmed exact release evidence.",
            }
        }
    }
    classifications, _reviewed = approve_research(document, decisions, sample_schema())
    assert classifications["tracks"][0]["placement_id"] == "fast:techno:hard-groove"


def test_approval_refuses_unaccepted_schema_proposals():
    document = researched_document()
    document["proposed_schema_changes"] = [
        {"action": "add_alias", "canonical_name": "Techno", "alias": "Techno Music"}
    ]
    with pytest.raises(ValueError, match="not been explicitly accepted"):
        approve_research(document, None, sample_schema())


def test_approval_refuses_a_track_still_marked_pending():
    document = researched_document()
    document["tracks"][0]["research_status"] = "pending"
    with pytest.raises(ValueError, match="research is still pending"):
        approve_research(document, None, sample_schema())


def test_metadata_modules_do_not_contain_voting_logic():
    for path in ("scripts/classification.py",):
        source = Path(path).read_text(encoding="utf-8").casefold()
        assert "weighted vote" not in source
        assert "consensusclassifier" not in source


def test_decisions_reject_unknown_tracks_and_approval_requires_evidence():
    with pytest.raises(ValueError, match="unknown Spotify track"):
        apply_decisions(
            researched_document(),
            {"tracks": [{"spotify_track_id": "missing", "main_genre": "Techno"}]},
        )

    document = researched_document()
    document["tracks"][0]["spotify_url"] = None
    document["tracks"][0]["provider_evidence"] = {}
    with pytest.raises(ValueError, match="direct evidence URL"):
        approve_research(document, None, sample_schema())


def test_approval_reports_missing_rationale_and_destination():
    document = researched_document()
    document["tracks"][0]["rationale"] = ""
    document["tracks"][0]["subgenre"] = "Unknown"
    with pytest.raises(ValueError) as error:
        approve_research(document, None, sample_schema())
    assert "research rationale is required" in str(error.value)
    assert "selected destination is not present" in str(error.value)
