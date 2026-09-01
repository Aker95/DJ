import pytest
from local_library import _apply_move_plan, _build_move_plan, build_delta


def test_delta_detects_missing_and_existing_tracks(tmp_path):
    folder = tmp_path / "music/fast/techno/hard groove"
    folder.mkdir(parents=True)
    (folder / "Artist - Existing.flac").write_bytes(b"audio")
    snapshot = {
        "playlists": [
            {
                "spotify_playlist_id": "leaf",
                "placement_id": "fast:techno:hard-groove",
                "master_folder": "FAST",
                "main_genre": "Techno",
                "subgenre": "Hard Groove",
                "local_folder": "music/fast/techno/hard groove",
                "tracks": [
                    {
                        "spotify_track_id": "one",
                        "artist": "Artist",
                        "title": "Existing",
                    },
                    {"spotify_track_id": "two", "artist": "Artist", "title": "Missing"},
                ],
            }
        ]
    }
    delta = build_delta(snapshot, tmp_path)
    assert [item["spotify_track_id"] for item in delta["tracks"]] == ["two"]


def test_move_plan_refuses_collision_and_verifies_hash(tmp_path):
    source = tmp_path / "music/source.flac"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    request = [{"source": "music/source.flac", "destination": "music/final.flac"}]
    plan = _build_move_plan(request, tmp_path)
    applied = _apply_move_plan(plan, tmp_path)
    assert len(applied) == 1
    assert (tmp_path / "music/final.flac").read_bytes() == b"audio"

    collision_source = tmp_path / "music/second.flac"
    collision_source.write_bytes(b"other")
    collision = _build_move_plan(
        [{"source": "music/second.flac", "destination": "music/final.flac"}], tmp_path
    )
    with pytest.raises(ValueError, match="collisions"):
        _apply_move_plan(collision, tmp_path)


def test_move_plan_refuses_a_source_changed_after_preview(tmp_path):
    source = tmp_path / "music/source.flac"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"first")
    plan = _build_move_plan(
        [{"source": "music/source.flac", "destination": "music/final.flac"}], tmp_path
    )
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        _apply_move_plan(plan, tmp_path)
