from __future__ import annotations

import pytest
from tests.helpers import sample_schema, settings_for

from dj_library.reconciliation import (
    apply_alignment_manifest,
    apply_move_manifest,
    audit_library,
    build_alignment_manifest,
    build_move_manifest,
)


class PlaylistFake:
    def __init__(self, memberships: dict[str, set[str]]) -> None:
        self.memberships = memberships
        self.calls: list[tuple[str, str]] = []

    def playlist_items(self, playlist_id, **_kwargs):
        return {
            "items": [
                {"item": {"id": value}} for value in self.memberships.get(playlist_id, set())
            ],
            "next": None,
        }

    def next(self, _page):
        return None

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


class FailingRemoveFake(PlaylistFake):
    fail_remove = True

    def _delete(self, endpoint, payload):
        if self.fail_remove:
            raise RuntimeError("simulated Spotify interruption")
        super()._delete(endpoint, payload)


def test_move_manifest_apply_and_retry(tmp_path):
    settings = settings_for(tmp_path)
    source = tmp_path / "music/intake/song.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    manifest = build_move_manifest(
        [{"source": "music/intake/song.mp3", "destination": "music/slow/house/song.mp3"}],
        settings=settings,
        purpose="test",
    )
    first = apply_move_manifest(manifest, settings=settings)
    second = apply_move_manifest(manifest, settings=settings)
    assert first["summary"] == {"moved": 1, "already_applied": 0}
    assert second["summary"] == {"moved": 0, "already_applied": 1}


def test_move_manifest_rejects_collision_and_changed_source(tmp_path):
    settings = settings_for(tmp_path)
    source = tmp_path / "music/a.mp3"
    target = tmp_path / "music/b.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"one")
    target.write_bytes(b"two")
    blocked = build_move_manifest(
        [{"source": "music/a.mp3", "destination": "music/b.mp3"}],
        settings=settings,
        purpose="test",
    )
    assert blocked["status"] == "blocked"
    with pytest.raises(ValueError, match="not safe"):
        apply_move_manifest(blocked, settings=settings)

    target.unlink()
    ready = build_move_manifest(
        [{"source": "music/a.mp3", "destination": "music/b.mp3"}],
        settings=settings,
        purpose="test",
    )
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        apply_move_manifest(ready, settings=settings)


def test_move_manifest_rejects_escape(tmp_path):
    settings = settings_for(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        build_move_manifest(
            [{"source": "../outside.mp3", "destination": "music/a.mp3"}],
            settings=settings,
            purpose="test",
        )


def test_alignment_preserves_add_move_remove_order(tmp_path):
    settings = settings_for(tmp_path)
    schema = sample_schema()
    source = tmp_path / "music/intake/song.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    from dj_library.artifacts import sha256_file

    review = {
        "tracks": [
            {
                "spotify_id": "track",
                "acquired_file": {
                    "intake_path": "music/intake/song.mp3",
                    "sha256": sha256_file(source),
                },
                "initial_track": {
                    "placement_id": "old",
                    "provisional_spotify_playlist_id": "old-list",
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
                "rationale": "Reviewed identity and BPM.",
            }
        ]
    }
    manifest = build_alignment_manifest(review, decisions, schema, settings=settings)
    fake = PlaylistFake({"old-list": {"track"}, "deep-house-playlist": set()})
    result = apply_alignment_manifest(manifest, spotify=fake, settings=settings)
    assert fake.calls == [("add", "deep-house-playlist"), ("remove", "old-list")]
    assert result["summary"]["aligned"] == 1
    assert (tmp_path / "music/slow/house/deep house/song.mp3").read_bytes() == b"audio"


def test_alignment_resumes_after_partial_external_failure(tmp_path):
    settings = settings_for(tmp_path)
    source = tmp_path / "music/intake/song.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    from dj_library.artifacts import sha256_file

    review = {
        "tracks": [
            {
                "spotify_id": "track",
                "acquired_file": {
                    "intake_path": "music/intake/song.mp3",
                    "sha256": sha256_file(source),
                },
                "initial_track": {"provisional_spotify_playlist_id": "old-list"},
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
                "rationale": "Reviewed identity and BPM.",
            }
        ]
    }
    manifest = build_alignment_manifest(review, decisions, sample_schema(), settings=settings)
    fake = FailingRemoveFake({"old-list": {"track"}, "deep-house-playlist": set()})
    with pytest.raises(RuntimeError, match="interruption"):
        apply_alignment_manifest(manifest, spotify=fake, settings=settings)
    assert (tmp_path / "music/slow/house/deep house/song.mp3").is_file()
    assert fake.memberships["deep-house-playlist"] == {"track"}
    fake.fail_remove = False
    result = apply_alignment_manifest(manifest, spotify=fake, settings=settings)
    assert result["tracks"][0]["local_status"] == "already_moved"
    assert fake.memberships["old-list"] == set()


def test_audit_separates_final_and_neutral_files(tmp_path):
    settings = settings_for(tmp_path)
    final = tmp_path / "music/slow/house/song.mp3"
    neutral = tmp_path / "music/review/problem.mp3"
    final.parent.mkdir(parents=True)
    neutral.parent.mkdir(parents=True)
    final.write_bytes(b"a")
    neutral.write_bytes(b"b")
    audit = audit_library(sample_schema(), settings=settings)
    assert audit["summary"]["final_audio_files"] == 1
    assert audit["summary"]["neutral_audio_files"] == 1
