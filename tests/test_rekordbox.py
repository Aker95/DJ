from pathlib import Path

from tests.helpers import config_for, sample_schema
from utils.rekordbox import export_xml, review_tracks


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
    review = review_tracks(tracks, sample_schema(), {"tracks": []})
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
    review = review_tracks(
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
    summary = export_xml(config, sample_schema(), output)
    assert summary["tracks"] == 1
    xml = output.read_text()
    assert "Track.flac" in xml
    assert "Ignored.flac" not in xml
