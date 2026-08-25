from __future__ import annotations

import pytest
from tests.helpers import sample_schema, settings_for

from dj_library.artifacts import sha256_file, write_json
from dj_library.sync import (
    apply_promotion,
    apply_unsorted_reconciliation,
    build_delta,
    build_unsorted_reconciliation,
    empty_registry,
    identity_check,
    load_registry,
    pending_acquisition_ids,
    record_verification,
    verify_acquisition,
    version_tokens,
)


def snapshot_with(track_id="track"):
    return {
        "playlists": [
            {
                "placement_id": "slow:house:deep-house",
                "playlist_id": "deep-house-playlist",
                "playlist_name": "Deep",
                "main_genre": "House",
                "subgenre": "Deep House",
                "classification_level": "subgenre",
                "master_folder": "01 • 118–125 WARM/GROOVE",
                "tracks": [
                    {
                        "spotify_track_id": track_id,
                        "artist": "Example",
                        "title": "Ocean",
                        "duration_ms": 180000,
                    }
                ],
            }
        ]
    }


def test_registry_defaults_pending_and_statuses(tmp_path):
    settings = settings_for(tmp_path)
    assert load_registry(settings.registry_path)["tracks"] == {}
    assert settings.registry_path.is_file()
    registry = empty_registry()
    record_verification(
        registry,
        {
            "tracks": [
                {"spotify_id": "a", "status": "verified_consistent"},
                {"spotify_id": "b", "status": "file_review_required"},
            ]
        },
    )
    assert pending_acquisition_ids(settings, registry) == {"a", "b"}
    write_json(settings.registry_path, registry)
    assert (
        load_registry(settings.registry_path)["tracks"]["a"]["workflow_status"]
        == "verified_consistent"
    )


def test_load_registry_rejects_wrong_contract(tmp_path):
    settings = settings_for(tmp_path)
    write_json(settings.registry_path, {"artifact_type": "wrong"})
    with pytest.raises(ValueError, match="Invalid"):
        load_registry(settings.registry_path)


def test_delta_then_reviewed_unsorted_move(tmp_path):
    settings = settings_for(tmp_path)
    unsorted = tmp_path / "music/loose"
    unsorted.mkdir(parents=True)
    (unsorted / "Example - Ocean.mp3").write_bytes(b"audio")
    delta = build_delta(snapshot_with(), sample_schema(), settings=settings)
    manifest, remaining = build_unsorted_reconciliation(delta, sample_schema(), settings=settings)
    assert remaining["summary"]["missing"] == 0
    output = tmp_path / "data/delta.json"
    result = apply_unsorted_reconciliation(manifest, settings=settings, delta_output=output)
    assert result["summary"]["moved"] == 1
    assert (tmp_path / "music/slow/house/deep house/Example - Ocean.mp3").exists()


def test_delta_skips_existing_and_pending(tmp_path):
    settings = settings_for(tmp_path)
    folder = tmp_path / "music/slow/house/deep house"
    folder.mkdir(parents=True)
    (folder / "Example - Ocean.mp3").write_bytes(b"x")
    assert build_delta(snapshot_with(), sample_schema(), settings=settings)["tracks"] == []
    (folder / "Example - Ocean.mp3").unlink()
    result = build_delta(snapshot_with(), sample_schema(), settings=settings, pending_ids={"track"})
    assert result["summary"]["pending_skipped"] == 1


def test_version_tokens_use_word_boundaries():
    assert version_tokens("Live Extended Mix") == {"live", "extended", "mix"}
    assert "live" not in version_tokens("Deliver Me")


def test_identity_check_detects_wrong_version(tmp_path):
    settings = settings_for(tmp_path)
    path = tmp_path / "music/intake/Example - Ocean (Live).mp3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    result = identity_check(
        {"artist": "Example", "title": "Ocean", "duration_ms": 180000},
        {
            "intake_path": "music/intake/Example - Ocean (Live).mp3",
            "duration_ms": 180000,
            "tags": {},
        },
        settings=settings,
    )
    assert "version-mismatch" in result["reasons"]


def test_verification_promotion_and_retry(tmp_path):
    settings = settings_for(tmp_path)
    path = tmp_path / "music/intake/run/Example - Ocean.mp3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"audio")
    manifest = {
        "run_id": "run",
        "tracks": [
            {
                "spotify_id": "track",
                "status": "downloaded_pending_analysis",
                "artist": "Example",
                "title": "Ocean",
                "duration_ms": 180000,
                "master_folder": "01 • 118–125 WARM/GROOVE",
                "placement_id": "slow:house:deep-house",
                "intended_local_folder": "music/slow/house/deep house",
                "acquired_file": {
                    "intake_path": "music/intake/run/Example - Ocean.mp3",
                    "file_name": "Example - Ocean.mp3",
                    "sha256": sha256_file(path),
                    "duration_ms": 180000,
                    "tags": {"artist": "Example", "title": "Ocean"},
                },
            }
        ],
    }

    def analyzer(_path, _schema):
        return {
            "bpm_ambiguous": False,
            "selected_master_folder": "01 • 118–125 WARM/GROOVE",
        }

    complete, promotion, review, cache = verify_acquisition(
        manifest, sample_schema(), settings=settings, analyzer=analyzer
    )
    assert complete["tracks"][0]["status"] == "verified_consistent"
    assert review["tracks"] == []
    result, registry = apply_promotion(promotion, settings=settings, registry=empty_registry())
    assert result["summary"]["moved"] == 1
    retry, _registry = apply_promotion(promotion, settings=settings, registry=registry)
    assert retry["summary"]["already_applied"] == 1
    assert cache
