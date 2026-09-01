import pytest
from acquisition import build_acquisition_plan
from classification import _prepare_document, approve_research
from local_library import (
    apply_reconciliation_plan,
    build_delta,
    build_reconciliation_plan,
)
from spotify import apply_sort_plan, build_snapshot, build_sort_plan

from tests.helpers import config_for, sample_schema
from tests.test_spotify import FakeSpotify


@pytest.mark.integration
def test_approved_track_flows_from_spotify_to_reconciled_local_library(tmp_path):
    catalog = sample_schema()
    spotify = FakeSpotify()
    document = _prepare_document(
        {"id": "source", "name": "Research Bag"},
        [
            {
                "spotify_track_id": "track-1",
                "artist": "Artist",
                "title": "Track",
                "duration_ms": 300000,
                "isrc": "CODE",
                "spotify_url": "https://open.spotify.com/track/track-1",
            }
        ],
        catalog,
    )
    document["tracks"][0].update(
        {
            "master_folder": "FAST",
            "main_genre": "Techno",
            "subgenre": "Hard Groove",
            "rationale": "Exact recording evidence supports the route.",
            "alternatives_considered": ["Techno parent"],
            "research_status": "reviewed",
        }
    )
    classifications, _reviewed = approve_research(document, None, catalog)
    apply_sort_plan(spotify, build_sort_plan(classifications))

    snapshot = build_snapshot(spotify, catalog)
    delta = build_delta(snapshot, tmp_path)
    assert delta["summary"] == {"checked": 1, "missing": 1}
    assert build_acquisition_plan(delta, catalog)["summary"]["tracks"] == 1

    unsorted = tmp_path / "music/unsorted"
    unsorted.mkdir(parents=True)
    (unsorted / "Artist - Track.flac").write_bytes(b"audio")
    reconciliation = build_reconciliation_plan(delta, config_for(tmp_path), catalog)
    assert reconciliation["safe_to_apply"] is True
    apply_reconciliation_plan(reconciliation, tmp_path)

    refreshed = build_delta(snapshot, tmp_path)
    assert refreshed["summary"] == {"checked": 1, "missing": 0}
