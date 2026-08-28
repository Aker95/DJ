from pathlib import Path

from acquire import acquire_tracks
from tests.helpers import config_for, sample_schema


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
        return [{"username": "user", "files": [{"filename": "Artist - Track.flac", "size": 1000, "length": 300}]}]

    def download(_config, _candidate, intake):
        intake.mkdir(parents=True)
        path = intake / "Artist - Track.flac"
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(
        "acquire.inspect_audio",
        lambda path: {"file_name": path.name, "size": 5, "sha256": "ignored", "duration_ms": 300000, "tags": {"artist": "Artist", "title": "Track"}},
    )
    result = acquire_tracks(
        delta,
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
        "acquire.inspect_audio",
        lambda path: {"file_name": path.name, "duration_ms": 300000, "tags": {"artist": "Artist", "title": "Track"}},
    )

    def download(_config, _candidate, intake):
        intake.mkdir(parents=True)
        path = intake / "Artist - Track.flac"
        path.write_bytes(b"audio")
        return path

    result = acquire_tracks(
        delta,
        config,
        sample_schema(),
        search=lambda *_args: [{"username": "u", "files": [{"filename": "Artist - Track.flac", "size": 1000, "length": 300}]}],
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
    result = acquire_tracks(
        delta,
        config,
        sample_schema(),
        search=lambda *_args: (_ for _ in ()).throw(AssertionError("must not search")),
    )
    assert result["tracks"][0]["review_flags"] == ["intake-collision"]
