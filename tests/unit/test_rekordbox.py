from __future__ import annotations

from xml.etree import ElementTree as ET

from tests.helpers import sample_schema, settings_for

from dj_library.rekordbox import (
    export_xml,
    fuzzy_score,
    match_spotify_track,
    normalize_bpm,
    reconcile,
    verify_structure,
)


def local_track(**changes):
    track = {
        "path": "music/slow/house/deep house/Example - Ocean.mp3",
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
    track.update(changes)
    return track


def reviewed_track(**changes):
    track = {
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
    track.update(changes)
    return track


def test_bpm_normalization():
    assert normalize_bpm(62) == (124.0, True)
    assert normalize_bpm(124) == (124.0, False)
    assert normalize_bpm(None) == (None, False)


def test_structure_verification_uses_rekordbox_bpm():
    verified = verify_structure([local_track()], sample_schema())
    assert verified["tracks"][0]["status"] == "consistent"
    mismatch = verify_structure([local_track(rekordbox_bpm=130)], sample_schema())
    assert "bpm-band-mismatch" in mismatch["tracks"][0]["review_flags"]


def test_identity_matching_prefers_isrc_and_rejects_version_mismatch():
    assert match_spotify_track(local_track(), [reviewed_track()])["method"] == "isrc"
    assert (
        fuzzy_score(
            local_track(isrc=None, title="Ocean Remix"),
            reviewed_track(isrc=None, title="Ocean"),
        )
        is None
    )


def test_reconcile_same_band_and_cross_band():
    reviewed = {"tracks": [reviewed_track()]}
    same = reconcile(sample_schema(), reviewed, [local_track()])
    assert same["records"][0]["status"] == "verified_same_band"
    cross = reconcile(sample_schema(), reviewed, [local_track(rekordbox_bpm=130)])
    assert cross["records"][0]["status"] == "cross_band_review"
    assert cross["records"][0]["proposed_route"]["status"] == "schema_topology_required"


def test_xml_exports_only_direct_canonical_files(tmp_path):
    settings = settings_for(tmp_path)
    final = tmp_path / "music/slow/house/deep house/Example - Ocean.mp3"
    review = tmp_path / "music/review/Do Not Export.mp3"
    final.parent.mkdir(parents=True)
    review.parent.mkdir(parents=True)
    final.write_bytes(b"not-real-audio")
    review.write_bytes(b"not-real-audio")
    output = tmp_path / "data/rekordbox.xml"
    summary = export_xml(sample_schema(), settings=settings, output_path=output)
    root = ET.parse(output).getroot()
    locations = [
        item.attrib["Location"] for item in root.findall(".//TRACK") if "Location" in item.attrib
    ]
    assert summary["tracks"] == 1
    assert all("review" not in value for value in locations)
