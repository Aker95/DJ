import pytest
from local_library import (
    _apply_move_plan,
    _build_move_plan,
    apply_reconciliation_plan,
    apply_structure_plan,
    build_delta,
    build_reconciliation_plan,
    build_structure_plan,
    promote_verified_file,
)

from tests.helpers import config_for, sample_schema


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


def test_structure_plan_creates_only_catalog_folders(tmp_path):
    plan = build_structure_plan(sample_schema(), tmp_path)
    assert plan["summary"]["folders"] == 2
    result = apply_structure_plan(plan, tmp_path)
    assert result["summary"]["folders"] == 2
    assert (tmp_path / "music/fast/techno/hard groove").is_dir()
    assert build_structure_plan(sample_schema(), tmp_path)["actions"] == []


def test_reconciliation_reports_unresolved_then_applies_unique_match(tmp_path):
    config = config_for(tmp_path)
    unsorted = tmp_path / "music/unsorted"
    unsorted.mkdir(parents=True)
    (unsorted / "Artist - Track.flac").write_bytes(b"audio")
    delta = {
        "tracks": [
            {
                "spotify_track_id": "one",
                "artist": "Artist",
                "title": "Track",
                "local_folder": "music/fast/techno/hard groove",
            },
            {
                "spotify_track_id": "two",
                "artist": "Nobody",
                "title": "Missing",
                "local_folder": "music/fast/techno/hard groove",
            },
        ]
    }
    plan = build_reconciliation_plan(delta, config, sample_schema())
    assert plan["summary"] == {"moves": 1, "collisions": 0, "unresolved": 1}
    assert plan["unresolved"][0]["reason"] == "no-match"
    result = apply_reconciliation_plan(plan, tmp_path)
    assert result["summary"]["moved"] == 1
    assert (tmp_path / "music/fast/techno/hard groove/Artist - Track.flac").is_file()


def test_verified_promotion_refuses_collision_and_escaped_paths(tmp_path):
    source = tmp_path / "music/intake/id/Track.flac"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    destination = tmp_path / "music/final"
    promoted = promote_verified_file(source, destination, tmp_path)
    assert promoted["destination"] == "music/final/Track.flac"

    second = tmp_path / "music/intake/id2/Track.flac"
    second.parent.mkdir(parents=True)
    second.write_bytes(b"other")
    with pytest.raises(FileExistsError, match="collision"):
        promote_verified_file(second, destination, tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        promote_verified_file(second, tmp_path.parent / "outside", tmp_path)
