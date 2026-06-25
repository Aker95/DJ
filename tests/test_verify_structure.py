"""
Tests for scripts/02_verify_structure.py
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import importlib
verify_mod = importlib.import_module("02_verify_structure")
config_mod = importlib.import_module("utils.config")


class TestVerifyStructureMain:
    def _run(self, tmp_path, schema):
        music_root = tmp_path / "music"
        music_root.mkdir(exist_ok=True)
        env = {"MUSIC_ROOT": str(music_root)}

        with (
            patch.object(verify_mod, "PROJECT_ROOT", tmp_path),
            patch.object(verify_mod, "load_env", return_value=env),
            patch.object(verify_mod, "load_schema", return_value=schema),
        ):
            verify_mod.main()

        return music_root

    # --- existing folders are left alone ---

    def test_existing_folder_not_recreated(self, tmp_path, sample_schema):
        music_root = tmp_path / "music"
        music_root.mkdir()
        (music_root / "afro house").mkdir()

        env = {"MUSIC_ROOT": str(music_root)}
        with (
            patch.object(verify_mod, "PROJECT_ROOT", tmp_path),
            patch.object(verify_mod, "load_env", return_value=env),
            patch.object(verify_mod, "load_schema", return_value=sample_schema),
        ):
            verify_mod.main()

        assert (music_root / "afro house").exists()

    # --- missing folders are created ---

    def test_creates_missing_schema_folder(self, tmp_path, sample_schema):
        music_root = self._run(tmp_path, sample_schema)
        assert (tmp_path / "music" / "afro house").exists()
        assert (tmp_path / "music" / "tech house").exists()
        assert (tmp_path / "music" / "bass house").exists()

    def test_creates_all_schema_folders(self, tmp_path, sample_schema):
        music_root = self._run(tmp_path, sample_schema)
        expected = ["afro house", "deep house", "tech house", "bass house"]
        for name in expected:
            assert (tmp_path / "music" / name).exists(), f"Missing: {name}"

    # --- null local_folder → derived from subgenre name ---

    def test_derives_path_for_null_local_folder(self, tmp_path, sample_schema):
        music_root = self._run(tmp_path, sample_schema)
        # "Melodic House" has local_folder: null → should create music/melodic house
        assert (tmp_path / "music" / "melodic house").exists()

    def test_derived_path_uses_lowercase(self, tmp_path):
        schema = {
            "master_folders": [{
                "id": "f1", "name": "TEST", "bpm_min": 120, "bpm_max": 130, "local_path": "music",
                "subgenres": [
                    {"name": "Hard Techno", "local_folder": None, "spotify_playlist": None, "notes": None},
                ]
            }]
        }
        music_root_path = tmp_path / "music"
        music_root_path.mkdir(exist_ok=True)
        env = {"MUSIC_ROOT": str(music_root_path)}
        with (
            patch.object(verify_mod, "PROJECT_ROOT", tmp_path),
            patch.object(verify_mod, "load_env", return_value=env),
            patch.object(verify_mod, "load_schema", return_value=schema),
        ):
            verify_mod.main()
        assert (tmp_path / "music" / "hard techno").exists()

    def test_derived_path_replaces_slash(self, tmp_path):
        schema = {
            "master_folders": [{
                "id": "f1", "name": "TEST", "bpm_min": 120, "bpm_max": 130, "local_path": "music",
                "subgenres": [
                    {"name": "Drum & Bass", "local_folder": None, "spotify_playlist": None, "notes": None},
                ]
            }]
        }
        music_root_path = tmp_path / "music"
        music_root_path.mkdir(exist_ok=True)
        env = {"MUSIC_ROOT": str(music_root_path)}
        with (
            patch.object(verify_mod, "PROJECT_ROOT", tmp_path),
            patch.object(verify_mod, "load_env", return_value=env),
            patch.object(verify_mod, "load_schema", return_value=schema),
        ):
            verify_mod.main()
        # "/" replaced with "-" → "drum & bass" (no slash)
        created = list((tmp_path / "music").iterdir())
        assert any("drum" in d.name for d in created)

    # --- path resolution ---

    def test_uses_project_root_for_local_folder_resolution(self, tmp_path, sample_schema):
        """local_folder "music/tech house" should resolve relative to PROJECT_ROOT, not music_root."""
        music_root = tmp_path / "music"
        music_root.mkdir()
        env = {"MUSIC_ROOT": str(music_root)}
        with (
            patch.object(verify_mod, "PROJECT_ROOT", tmp_path),
            patch.object(verify_mod, "load_env", return_value=env),
            patch.object(verify_mod, "load_schema", return_value=sample_schema),
        ):
            verify_mod.main()

        # "music/tech house" relative to tmp_path → tmp_path/music/tech house
        assert (tmp_path / "music" / "tech house").exists()

    # --- idempotency ---

    def test_idempotent_run_twice(self, tmp_path, sample_schema):
        """Running twice should not raise or change the result."""
        env = {"MUSIC_ROOT": str(tmp_path / "music")}
        kwargs = dict(
            patch_obj=patch.object(verify_mod, "PROJECT_ROOT", tmp_path),
        )
        for _ in range(2):
            with (
                patch.object(verify_mod, "PROJECT_ROOT", tmp_path),
                patch.object(verify_mod, "load_env", return_value=env),
                patch.object(verify_mod, "load_schema", return_value=sample_schema),
            ):
                verify_mod.main()

        assert (tmp_path / "music" / "afro house").exists()

    # --- empty schema ---

    def test_empty_schema_no_folders_created(self, tmp_path):
        schema = {"master_folders": []}
        env = {"MUSIC_ROOT": str(tmp_path / "music")}
        music = tmp_path / "music"
        music.mkdir()
        with (
            patch.object(verify_mod, "PROJECT_ROOT", tmp_path),
            patch.object(verify_mod, "load_env", return_value=env),
            patch.object(verify_mod, "load_schema", return_value=schema),
        ):
            verify_mod.main()
        assert list(music.iterdir()) == []
