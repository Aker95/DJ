"""Safety tests for the manifest-driven local folder migration."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
spec = importlib.util.spec_from_file_location(
    "genre_hierarchy_migration", SCRIPTS_DIR / "migrate_to_nested_folders.py"
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def write_schema(root: Path) -> Path:
    schema = {
        "_meta": {"version": "2.0.0", "folder_migration_status": "pending_review"},
        "main_genres": [
            {"id": "trance", "name": "Trance", "aliases": [], "spotify_playlist": None}
        ],
        "master_folders": [
            {
                "id": "fast",
                "name": "Fast",
                "local_path": "music/fast",
                "genre_placements": [
                    {
                        "main_genre_id": "trance",
                        "local_folder": "music/fast/trance",
                        "migration_status": "complete",
                        "subgenres": [
                            {
                                "id": "hard-trance",
                                "name": "Hard Trance",
                                "aliases": [],
                                "legacy_local_folder": "music/fast/hard-trance",
                                "local_folder": "music/fast/trance/hard-trance",
                                "migration_status": "pending",
                                "spotify_playlist": None,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    path = root / "music_schema_knowledge.json"
    path.write_text(json.dumps(schema), encoding="utf-8")
    return path


def test_dry_run_is_non_mutating_and_counts_only_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "PROJECT_ROOT", tmp_path)
    schema_path = write_schema(tmp_path)
    source = tmp_path / "music/fast/hard-trance"
    source.mkdir(parents=True)
    (source / "track.mp3").write_bytes(b"audio")
    (source / "notes.txt").write_text("leave me")

    manifest = migration.build_manifest(schema_path)

    assert manifest["status"] == "ready_for_review"
    assert manifest["summary"]["audio_files"] == 1
    assert (source / "track.mp3").exists()
    assert not (tmp_path / "music/fast/trance/hard-trance/track.mp3").exists()


def test_apply_requires_exact_hash_and_is_resumable(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "PROJECT_ROOT", tmp_path)
    schema_path = write_schema(tmp_path)
    source = tmp_path / "music/fast/hard-trance"
    source.mkdir(parents=True)
    (source / "track.mp3").write_bytes(b"audio")
    manifest = migration.build_manifest(schema_path)
    manifest_path = tmp_path / "manifest.json"
    migration.write_manifest(manifest, manifest_path)

    result = migration.apply_manifest(manifest_path)
    assert result["moved"] == 1
    assert source.exists()  # old directories are deliberately preserved
    assert (tmp_path / "music/fast/trance/hard-trance/track.mp3").exists()

    repeated = migration.apply_manifest(manifest_path)
    assert repeated == {
        "moved": 0,
        "already_applied": 1,
        "schema_updated": False,
        "old_directories_deleted": False,
    }

    # Simulate an interrupted run whose files were moved but schema update was not.
    schema = json.loads(schema_path.read_text())
    node = schema["master_folders"][0]["genre_placements"][0]["subgenres"][0]
    node["migration_status"] = "pending"
    node["legacy_local_folder"] = "music/fast/hard-trance"
    schema["_meta"]["folder_migration_status"] = "pending_review"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    manifest["schema_sha256_before"] = migration._file_sha256(schema_path)
    manifest["manifest_sha256"] = migration._json_sha256(manifest)
    migration.write_manifest(manifest, manifest_path)
    resumed = migration.apply_manifest(manifest_path)
    assert resumed["already_applied"] == 1


def test_collision_blocks_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "PROJECT_ROOT", tmp_path)
    schema_path = write_schema(tmp_path)
    source = tmp_path / "music/fast/hard-trance"
    destination = tmp_path / "music/fast/trance/hard-trance"
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    (source / "track.mp3").write_bytes(b"old")
    (destination / "track.mp3").write_bytes(b"new")
    manifest = migration.build_manifest(schema_path)
    manifest_path = tmp_path / "manifest.json"
    migration.write_manifest(manifest, manifest_path)
    assert manifest["status"] == "blocked_by_collisions"
    with pytest.raises(ValueError, match="contains collisions"):
        migration.apply_manifest(manifest_path)
