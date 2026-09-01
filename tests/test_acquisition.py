import acquisition
import requests
from acquisition import apply_acquisition_plan, build_acquisition_plan

from tests.helpers import config_for, sample_schema


def _delta(local_folder="music/fast/techno/hard groove"):
    return {
        "tracks": [
            {
                "spotify_track_id": "track-1",
                "artist": "Artist",
                "title": "Track",
                "duration_ms": 300000,
                "master_folder": "FAST",
                "main_genre": "Techno",
                "subgenre": "Hard Groove",
                "local_folder": local_folder,
            }
        ]
    }


def _search_result():
    return [
        {
            "username": "user",
            "files": [{"filename": "Artist - Track.flac", "size": 1000, "length": 300}],
        }
    ]


def _download(_config, _candidate, intake):
    intake.mkdir(parents=True)
    path = intake / "Artist - Track.flac"
    path.write_bytes(b"audio")
    return path


def _metadata(path):
    return {
        "file_name": path.name,
        "size": 5,
        "sha256": "ignored",
        "duration_ms": 300000,
        "tags": {"artist": "Artist", "title": "Track"},
    }


def test_safe_acquisition_is_promoted(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    destination = tmp_path / "music/fast/techno/hard groove"
    destination.mkdir(parents=True)
    delta = {
        "tracks": [
            {
                "spotify_track_id": "track-1",
                "artist": "Artist",
                "title": "Track",
                "duration_ms": 300000,
                "master_folder": "FAST",
                "main_genre": "Techno",
                "subgenre": "Hard Groove",
                "local_folder": "music/fast/techno/hard groove",
            }
        ]
    }

    def search(_config, _artist, _title):
        return [
            {
                "username": "user",
                "files": [
                    {"filename": "Artist - Track.flac", "size": 1000, "length": 300}
                ],
            }
        ]

    def download(_config, _candidate, intake):
        intake.mkdir(parents=True)
        path = intake / "Artist - Track.flac"
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(
        "acquisition._inspect_audio",
        lambda path: {
            "file_name": path.name,
            "size": 5,
            "sha256": "ignored",
            "duration_ms": 300000,
            "tags": {"artist": "Artist", "title": "Track"},
        },
    )
    result = apply_acquisition_plan(
        build_acquisition_plan(delta, sample_schema()),
        config,
        sample_schema(),
        search=search,
        download=download,
        analyze=lambda _path: {"raw_bpm": 136.0, "bpm_estimates": [136.1]},
    )
    assert result["summary"] == {"tracks": 1, "promoted": 1, "review": 0}
    assert (destination / "Artist - Track.flac").is_file()


def test_ambiguous_bpm_remains_in_intake(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    delta = {
        "tracks": [
            {
                "spotify_track_id": "track-1",
                "artist": "Artist",
                "title": "Track",
                "duration_ms": 300000,
                "master_folder": "FAST",
                "main_genre": "Techno",
                "subgenre": "Hard Groove",
                "local_folder": "music/fast/techno/hard groove",
            }
        ]
    }
    monkeypatch.setattr(
        "acquisition._inspect_audio",
        lambda path: {
            "file_name": path.name,
            "duration_ms": 300000,
            "tags": {"artist": "Artist", "title": "Track"},
        },
    )

    def download(_config, _candidate, intake):
        intake.mkdir(parents=True)
        path = intake / "Artist - Track.flac"
        path.write_bytes(b"audio")
        return path

    result = apply_acquisition_plan(
        build_acquisition_plan(delta, sample_schema()),
        config,
        sample_schema(),
        search=lambda *_args: [
            {
                "username": "u",
                "files": [
                    {"filename": "Artist - Track.flac", "size": 1000, "length": 300}
                ],
            }
        ],
        download=download,
        analyze=lambda _path: {"raw_bpm": 100.0, "bpm_estimates": []},
    )
    assert result["summary"]["review"] == 1
    assert (tmp_path / "music/intake/track-1/Artist - Track.flac").is_file()


def test_existing_intake_file_is_a_collision_not_a_reused_download(tmp_path):
    config = config_for(tmp_path)
    intake = tmp_path / "music/intake/track-1"
    intake.mkdir(parents=True)
    (intake / "Artist - Track.flac").write_bytes(b"existing")
    delta = {
        "tracks": [
            {
                "spotify_track_id": "track-1",
                "artist": "Artist",
                "title": "Track",
                "master_folder": "FAST",
                "main_genre": "Techno",
                "subgenre": "Hard Groove",
                "local_folder": "music/fast/techno/hard groove",
            }
        ]
    }
    result = apply_acquisition_plan(
        build_acquisition_plan(delta, sample_schema()),
        config,
        sample_schema(),
        search=lambda *_args: (_ for _ in ()).throw(AssertionError("must not search")),
    )
    assert result["tracks"][0]["review_flags"] == ["intake-collision"]


def test_invalid_destination_and_no_candidate_are_reported_without_promotion(tmp_path):
    config = config_for(tmp_path)
    invalid = apply_acquisition_plan(
        build_acquisition_plan(_delta("music/wrong"), sample_schema()),
        config,
        sample_schema(),
        search=lambda *_args: (_ for _ in ()).throw(AssertionError("must not search")),
    )
    assert invalid["tracks"][0]["review_flags"] == ["intended-destination-invalid"]

    missing = apply_acquisition_plan(
        build_acquisition_plan(_delta(), sample_schema()),
        config,
        sample_schema(),
        search=lambda *_args: [],
    )
    assert missing["tracks"][0]["status"] == "download-failed"
    assert missing["tracks"][0]["review_flags"] == ["no-candidate-downloaded"]


def test_download_and_analysis_failures_remain_reviewable(tmp_path):
    config = config_for(tmp_path)
    plan = build_acquisition_plan(_delta(), sample_schema())
    failed_download = apply_acquisition_plan(
        plan,
        config,
        sample_schema(),
        search=lambda *_args: _search_result(),
        download=lambda *_args: (_ for _ in ()).throw(
            requests.RequestException("offline")
        ),
    )
    assert failed_download["tracks"][0]["review_flags"] == ["download-error"]

    failed_analysis = apply_acquisition_plan(
        plan,
        config,
        sample_schema(),
        search=lambda *_args: _search_result(),
        download=_download,
        analyze=lambda _path: (_ for _ in ()).throw(RuntimeError("broken analysis")),
    )
    assert failed_analysis["tracks"][0]["review_flags"] == ["audio-analysis-failed"]
    assert "broken analysis" in failed_analysis["tracks"][0]["error"]


def test_successful_acquisition_delegates_promotion_to_local_library(
    tmp_path, monkeypatch
):
    config = config_for(tmp_path)
    destination = tmp_path / "music/fast/techno/hard groove"
    destination.mkdir(parents=True)
    monkeypatch.setattr("acquisition._inspect_audio", _metadata)
    original = acquisition.promote_verified_file
    calls = []

    def promote(source, destination_folder, project_root):
        calls.append((source, destination_folder, project_root))
        return original(source, destination_folder, project_root)

    monkeypatch.setattr("acquisition.promote_verified_file", promote)
    result = apply_acquisition_plan(
        build_acquisition_plan(_delta(), sample_schema()),
        config,
        sample_schema(),
        search=lambda *_args: _search_result(),
        download=_download,
        analyze=lambda _path: {"raw_bpm": 136.0, "bpm_estimates": [136.0]},
    )
    assert result["tracks"][0]["status"] == "promoted"
    assert len(calls) == 1
    assert calls[0][1] == destination


def test_destination_collision_keeps_download_in_intake(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    destination = tmp_path / "music/fast/techno/hard groove"
    destination.mkdir(parents=True)
    (destination / "Artist - Track.flac").write_bytes(b"existing")
    monkeypatch.setattr("acquisition._inspect_audio", _metadata)
    result = apply_acquisition_plan(
        build_acquisition_plan(_delta(), sample_schema()),
        config,
        sample_schema(),
        search=lambda *_args: _search_result(),
        download=_download,
        analyze=lambda _path: {"raw_bpm": 136.0, "bpm_estimates": [136.0]},
    )
    assert result["tracks"][0]["review_flags"] == ["destination-collision"]
    assert (tmp_path / "music/intake/track-1/Artist - Track.flac").is_file()
