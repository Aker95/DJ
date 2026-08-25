from __future__ import annotations

from types import SimpleNamespace

from tests.helpers import sample_schema

from dj_library import audio
from dj_library.audio import inspect_audio, normalize_bpm_candidates


def test_raw_bpm_stays_in_band():
    result = normalize_bpm_candidates([{"bpm": 128, "weight": 1}], sample_schema())
    assert result[0]["bpm"] == 128
    assert result[0]["transform"] == "raw"


def test_half_time_is_doubled():
    result = normalize_bpm_candidates([{"bpm": 64, "weight": 1}], sample_schema())
    assert result[0]["bpm"] == 128
    assert result[0]["transform"] == "double-time"


def test_out_of_range_candidates_are_removed():
    assert normalize_bpm_candidates([{"bpm": 50, "weight": 1}], sample_schema()) == []


def test_duplicate_candidates_keep_stronger_weight():
    result = normalize_bpm_candidates(
        [{"bpm": 128, "weight": 0.2}, {"bpm": 128.5, "weight": 0.9}],
        sample_schema(),
    )
    assert len(result) == 1
    assert result[0]["weight"] == 0.9


def test_inspect_audio_reads_facts_and_normalizes_tags(tmp_path, monkeypatch):
    path = tmp_path / "song.mp3"
    path.write_bytes(b"audio")
    fake = SimpleNamespace(
        info=SimpleNamespace(length=180.4, bitrate=320000),
        tags={"artist": ["Example"], "title": "Ocean", "album": [], "isrc": None},
    )
    monkeypatch.setattr(audio, "MutagenFile", lambda *_args, **_kwargs: fake)
    result = inspect_audio(path)
    assert result["duration_ms"] == 180400
    assert result["bitrate_kbps"] == 320
    assert result["tags"] == {"artist": "Example", "title": "Ocean"}


def test_inspect_audio_preserves_hash_when_metadata_fails(tmp_path, monkeypatch):
    path = tmp_path / "broken.wav"
    path.write_bytes(b"audio")
    monkeypatch.setattr(audio, "MutagenFile", lambda *_args, **_kwargs: None)
    result = inspect_audio(path)
    assert result["duration_ms"] is None
    assert result["sha256"]
