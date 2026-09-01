from rekordbox import _match_classification, export_library, review_library

from tests.helpers import config_for, sample_schema


def test_rekordbox_review_flags_bpm_and_classification_path():
    tracks = [
        {
            "path": "music/fast/techno/hard groove/Track.flac",
            "artist": "Artist",
            "title": "Track",
            "duration_ms": 300000,
            "isrc": "CODE",
            "rekordbox_bpm": 150.0,
            "current_destination": {
                "placement_id": "fast:techno:hard-groove",
                "master_folder": "FAST",
            },
        }
    ]
    review = review_library(tracks, sample_schema(), {"tracks": []})
    assert "bpm-band-mismatch" in review["review_tracks"][0]["review_flags"]


def test_rekordbox_review_includes_acquisition_version_feedback():
    track = {
        "path": "music/fast/techno/hard groove/Track.flac",
        "artist": "Artist",
        "title": "Track",
        "duration_ms": 300000,
        "isrc": "CODE",
        "rekordbox_bpm": 136.0,
        "current_destination": {
            "placement_id": "fast:techno:hard-groove",
            "master_folder": "FAST",
        },
    }
    classification = {
        **track,
        "spotify_track_id": "track-1",
        "placement_id": "fast:techno:hard-groove",
        "master_folder": "FAST",
    }
    acquisitions = {
        "tracks": [
            {"spotify_track_id": "track-1", "review_flags": ["version-mismatch"]}
        ]
    }
    review = review_library(
        [track], sample_schema(), {"tracks": [classification]}, acquisitions
    )
    assert "possible-wrong-version" in review["review_tracks"][0]["review_flags"]


def test_rekordbox_xml_exports_only_direct_final_files(tmp_path):
    config = config_for(tmp_path)
    folder = tmp_path / "music/fast/techno/hard groove"
    folder.mkdir(parents=True)
    (folder / "Track.flac").write_bytes(b"not-real-audio")
    intake = tmp_path / "music/intake/id"
    intake.mkdir(parents=True)
    (intake / "Ignored.flac").write_bytes(b"audio")
    output = tmp_path / "rekordbox.xml"
    summary = export_library(config, sample_schema(), output)
    assert summary["tracks"] == 1
    xml = output.read_text()
    assert "Track.flac" in xml
    assert "Ignored.flac" not in xml


def test_rekordbox_identity_matching_reports_unmatched_and_ambiguous():
    local = {
        "artist": "Artist",
        "title": "Track",
        "duration_ms": 300000,
        "isrc": None,
    }
    assert _match_classification(local, [])["status"] == "unmatched"
    candidates = [
        {"artist": "Artist", "title": "Track", "duration_ms": 300000},
        {"artist": "Artist", "title": "Track", "duration_ms": 300100},
    ]
    assert _match_classification(local, candidates)["status"] == "ambiguous"


def test_rekordbox_review_flags_missing_path_bpm_and_ambiguous_identity():
    local = {
        "path": "music/unsorted/Track.flac",
        "artist": "Artist",
        "title": "Track",
        "duration_ms": 300000,
        "isrc": None,
        "rekordbox_bpm": None,
        "current_destination": None,
    }
    classifications = {
        "tracks": [
            {"artist": "Artist", "title": "Track", "duration_ms": 300000},
            {"artist": "Artist", "title": "Track", "duration_ms": 300100},
        ]
    }
    record = review_library([local], sample_schema(), classifications)["review_tracks"][
        0
    ]
    assert set(record["review_flags"]) == {
        "schema-path-missing",
        "rekordbox-bpm-missing",
        "identity-ambiguous",
    }
