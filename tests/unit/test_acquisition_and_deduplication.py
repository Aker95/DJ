from __future__ import annotations

from tests.helpers import settings_for

from dj_library import acquisition, deduplication
from dj_library.acquisition import apply_acquisition_plan, build_acquisition_plan, rank_candidates
from dj_library.deduplication import (
    apply_deduplication_manifest,
    build_deduplication_manifest,
    find_duplicate_groups,
    normalize_identity,
    normalize_title,
)


def test_candidate_ranking_filters_quality_and_prefers_lossless():
    results = [
        {
            "username": "u",
            "files": [
                {"filename": "Example - Ocean.mp3", "size": 3_000_000, "length": 180},
                {"filename": "Example - Ocean.flac", "size": 30_000_000, "length": 180},
                {"filename": "Wrong - Song.flac", "size": 40_000_000, "length": 180},
            ],
        }
    ]
    ranked = rank_candidates(
        results,
        min_bitrate_kbps=128,
        preferred_formats=("flac", "mp3"),
        artist="Example",
        title="Ocean",
    )
    assert [item["format"] for item in ranked] == ["flac", "mp3"]


def test_acquisition_plan_and_injected_apply(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    delta = {
        "tracks": [
            {
                "spotify_id": "track",
                "artist": "Example",
                "title": "Ocean",
                "local_folder": "music/final",
            }
        ]
    }
    plan = build_acquisition_plan(delta, settings=settings, run_id="run")

    def download(_settings, _candidate, intake):
        intake.mkdir(parents=True)
        path = intake / "Example - Ocean.flac"
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(
        acquisition, "inspect_audio", lambda path: {"file_name": path.name, "sha256": "hash"}
    )
    result = apply_acquisition_plan(
        plan,
        settings=settings,
        ensure_ready=lambda _settings: None,
        search=lambda _settings, _query: [
            {
                "username": "u",
                "files": [{"filename": "Example - Ocean.flac", "size": 1, "length": 1}],
            }
        ],
        download=download,
    )
    assert result["summary"]["downloaded"] == 1
    assert result["tracks"][0]["acquired_file"]["intake_path"].startswith("music/intake/run")
    resumed = apply_acquisition_plan(
        plan,
        settings=settings,
        ensure_ready=lambda _settings: None,
        search=lambda _settings, _query: (_ for _ in ()).throw(
            AssertionError("resume must not search")
        ),
        download=download,
    )
    assert resumed["tracks"][0]["acquisition_source"] == {"provider": "existing-intake"}


def candidate(path, extension, duration=180000, bitrate=0):
    return {
        "path": path,
        "extension": extension,
        "duration_ms": duration,
        "identity": ["example", "ocean"],
        "bits_per_sample": 16,
        "sample_rate": 44100,
        "bitrate": bitrate,
        "channels": 2,
        "source_penalty": 0,
        "tag_count": 2,
        "isrc": None,
        "explicit_duplicate_suffix": False,
    }


def test_duplicate_logic_prefers_lossless_and_preserves_uncertain():
    mp3 = candidate("a.mp3", ".mp3", bitrate=320000)
    flac = candidate("b.flac", ".flac")
    groups, uncertain = find_duplicate_groups([mp3, flac])
    assert groups[0]["keep"]["path"] == "b.flac"
    changed = candidate("c.mp3", ".mp3", duration=190000)
    groups, uncertain = find_duplicate_groups([mp3, changed])
    assert groups == [] and uncertain


def test_duplicate_normalization_removes_sources_and_original_mix():
    assert normalize_identity("SpotiDownloader.com Artist") == "artist"
    assert normalize_title("Ocean (Original Mix)") == "ocean"


def test_deduplication_builds_and_applies_recoverable_manifest(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    mp3_path = tmp_path / "music/slow/house/Ocean.mp3"
    flac_path = tmp_path / "music/slow/house/Ocean.flac"
    mp3_path.parent.mkdir(parents=True)
    mp3_path.write_bytes(b"mp3")
    flac_path.write_bytes(b"flac")

    def inspect(path, _settings):
        item = candidate(
            str(path.relative_to(tmp_path)),
            path.suffix,
            bitrate=320000 if path.suffix == ".mp3" else 0,
        )
        item.update({"filename": path.name, "size": path.stat().st_size, "sha256": "unused"})
        return item

    monkeypatch.setattr(deduplication, "inspect_candidate", inspect)
    quarantine = tmp_path / "music/review/dedup/run"
    manifest = build_deduplication_manifest(settings, quarantine_root=quarantine)
    assert manifest["summary"]["files_to_quarantine"] == 1
    destination = manifest["move_manifest"]["moves"][0]["destination"]
    assert destination == "music/review/dedup/run/slow/house/Ocean.mp3"
    result = apply_deduplication_manifest(manifest, settings=settings)
    assert result["summary"]["moved"] == 1
    assert (tmp_path / destination).read_bytes() == b"mp3"
