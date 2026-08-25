from __future__ import annotations

import pytest

from dj_library import acquisition
from dj_library.acquisition import apply_acquisition_plan, build_acquisition_plan
from dj_library.artifacts import sha256_file
from dj_library.reconciliation import apply_alignment_manifest, build_alignment_manifest
from dj_library.rekordbox import reconcile, verify_structure
from dj_library.research import approve_research, prepare_research
from dj_library.spotify import apply_sort_manifest, build_snapshot, build_sort_manifest
from dj_library.sync import apply_promotion, build_delta, empty_registry, verify_acquisition
from tests.helpers import sample_schema, settings_for

pytestmark = pytest.mark.integration


class WorkflowSpotify:
    def __init__(self):
        self.playlists = {
            "source": {"id": "source", "name": "Research Queue"},
            "deep-house-playlist": {
                "id": "deep-house-playlist",
                "name": "01 • 118–125 WARM/GROOVE • House • Deep House",
            },
        }
        self.memberships = {"source": {"track"}, "deep-house-playlist": set()}
        self.calls = []

    def current_user_playlists(self, limit=50):
        return {"items": list(self.playlists.values()), "next": None}

    def next(self, _page):
        return None

    def playlist(self, playlist_id, fields=None):
        return self.playlists[playlist_id]

    def playlist_items(self, playlist_id, **_kwargs):
        return {
            "items": [
                {
                    "item": {
                        "id": track_id,
                        "name": "Ocean",
                        "artists": [{"id": "artist", "name": "Example"}],
                        "album": {"id": "album", "name": "Release", "release_date": "2026"},
                        "duration_ms": 180000,
                        "external_ids": {"isrc": "XX123"},
                        "external_urls": {"spotify": "https://example.test/track"},
                    }
                }
                for track_id in self.memberships.get(playlist_id, set())
            ],
            "next": None,
        }

    def _post(self, endpoint, payload):
        playlist_id = endpoint.split("/")[1]
        self.calls.append(("add", playlist_id))
        self.memberships.setdefault(playlist_id, set()).update(
            uri.rsplit(":", 1)[-1] for uri in payload["uris"]
        )

    def _delete(self, endpoint, payload):
        playlist_id = endpoint.split("/")[1]
        self.calls.append(("remove", playlist_id))
        self.memberships.setdefault(playlist_id, set()).difference_update(
            item["uri"].rsplit(":", 1)[-1] for item in payload["items"]
        )


def decision(track_id="track"):
    return {
        "tracks": [
            {
                "spotify_track_id": track_id,
                "main_genre": "House",
                "subgenre": "Deep House",
                "master_folder": "01 • 118–125 WARM/GROOVE",
                "metadata_bpm": 124,
                "bpm_basis": "metadata",
                "confidence": 0.95,
                "rationale": "Exact release and store evidence agree.",
                "evidence": [{"url": "https://example.test/evidence"}],
                "review_flags": [],
            }
        ]
    }


def test_research_to_approval_to_spotify_sort_plan_and_apply():
    spotify = WorkflowSpotify()
    prepared = prepare_research("Research Queue", spotify=spotify, schema=sample_schema())
    results, _schema, _approval = approve_research(prepared, sample_schema(), decisions=decision())
    manifest = build_sort_manifest(results)
    applied = apply_sort_manifest(manifest, spotify=spotify)
    assert applied["summary"] == {"satisfied": 1, "added": 1}
    assert spotify.memberships["source"] == set()
    assert spotify.memberships["deep-house-playlist"] == {"track"}


def test_snapshot_delta_acquisition_verification_promotion(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    spotify = WorkflowSpotify()
    spotify.memberships["source"] = set()
    spotify.memberships["deep-house-playlist"] = {"track"}
    snapshot = build_snapshot(spotify, sample_schema())
    delta = build_delta(snapshot, sample_schema(), settings=settings)
    plan = build_acquisition_plan(delta, settings=settings, run_id="run")

    def download(_settings, _candidate, intake):
        intake.mkdir(parents=True)
        path = intake / "Example - Ocean.flac"
        path.write_bytes(b"audio")
        return path

    def inspect(path):
        return {
            "file_name": path.name,
            "sha256": sha256_file(path),
            "duration_ms": 180000,
            "tags": {"artist": "Example", "title": "Ocean"},
        }

    monkeypatch.setattr(acquisition, "inspect_audio", inspect)
    acquired = apply_acquisition_plan(
        plan,
        settings=settings,
        ensure_ready=lambda _settings: None,
        search=lambda _settings, _query: [
            {
                "username": "u",
                "files": [{"filename": "Example - Ocean.flac", "size": 1, "length": 1}],
            }
        ],
        download=download,
    )

    def analyzer(_path, _schema):
        return {
            "bpm_ambiguous": False,
            "selected_master_folder": "01 • 118–125 WARM/GROOVE",
        }

    complete, promotion, review, _cache = verify_acquisition(
        acquired,
        sample_schema(),
        settings=settings,
        analyzer=analyzer,
    )
    assert complete["summary"]["verified"] == 1 and review["tracks"] == []
    applied, registry = apply_promotion(promotion, settings=settings, registry=empty_registry())
    assert applied["summary"]["moved"] == 1
    assert registry["tracks"]["track"]["workflow_status"] == "final_aligned"


def test_rekordbox_verification_reconciliation_alignment(tmp_path):
    settings = settings_for(tmp_path)
    schema = sample_schema()
    source = tmp_path / "music/intake/Example - Ocean.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    local = {
        "path": "music/intake/Example - Ocean.mp3",
        "artist": "Example",
        "title": "Ocean",
        "duration_ms": 180000,
        "isrc": "XX123",
        "rekordbox_bpm": 124,
        "current_destination": {
            "placement_id": "slow:house:deep-house",
            "master_folder": "01 • 118–125 WARM/GROOVE",
            "main_genre": "House",
            "subgenre": "Deep House",
        },
    }
    verification = verify_structure([local], schema)
    reviewed = {
        "tracks": [
            {
                "spotify_track_id": "track",
                "artist": "Example",
                "title": "Ocean",
                "duration_ms": 180000,
                "isrc": "XX123",
                "master_folder": "01 • 118–125 WARM/GROOVE",
                "main_genre": "House",
                "subgenre": "Deep House",
                "target_playlist_id": "deep-house-playlist",
            }
        ]
    }
    reconciled = reconcile(schema, reviewed, verification["tracks"])
    assert reconciled["records"][0]["status"] == "verified_same_band"
    review = {
        "tracks": [
            {
                "spotify_id": "track",
                "acquired_file": {"intake_path": local["path"], "sha256": sha256_file(source)},
                "initial_track": {
                    "placement_id": "old",
                    "provisional_spotify_playlist_id": "source",
                },
            }
        ]
    }
    decisions = {
        "tracks": [
            {
                "spotify_id": "track",
                "master_folder": "01 • 118–125 WARM/GROOVE",
                "main_genre": "House",
                "subgenre": "Deep House",
                "rationale": "Identity and Rekordbox BPM verified.",
            }
        ]
    }
    manifest = build_alignment_manifest(review, decisions, schema, settings=settings)
    spotify = WorkflowSpotify()
    result = apply_alignment_manifest(manifest, spotify=spotify, settings=settings)
    assert result["summary"]["aligned"] == 1
    assert spotify.calls == [("add", "deep-house-playlist"), ("remove", "source")]
