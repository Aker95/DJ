import json
import subprocess
import sys
from pathlib import Path

import pytest
from artifacts import fingerprint_artifact, read_artifact, write_artifact
from catalog import apply_update_plan, build_update_plan

from tests.helpers import sample_schema


def test_artifact_kind_and_version_are_checked(tmp_path):
    path = tmp_path / "artifact.json"
    write_artifact(path, "research", {"tracks": []})
    assert read_artifact(path, "research")["tracks"] == []
    with pytest.raises(ValueError, match="Expected 'classifications'"):
        read_artifact(path, "classifications")
    data = read_artifact(path)
    data["_artifact"]["version"] = 999
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        read_artifact(path, "research")


def test_artifact_fingerprint_ignores_json_formatting(tmp_path):
    path = tmp_path / "artifact.json"
    write_artifact(path, "research", {"tracks": [{"id": "one"}]})
    expected = fingerprint_artifact(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    assert fingerprint_artifact(path) == expected


def test_catalog_update_rejects_a_stale_plan(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(sample_schema()), encoding="utf-8")
    plan = build_update_plan(sample_schema(), [])
    changed = sample_schema()
    changed["_meta"]["last_updated"] = "later"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="stale"):
        apply_update_plan(path, plan)


def test_only_deep_modules_remain_and_all_clis_have_offline_help():
    root = Path(__file__).resolve().parents[1]
    scripts = root / "scripts"
    expected = {
        "acquisition.py",
        "artifacts.py",
        "catalog.py",
        "classification.py",
        "config.py",
        "local_library.py",
        "matching.py",
        "rekordbox.py",
        "spotify.py",
    }
    assert {path.name for path in scripts.glob("*.py")} == expected
    assert not (scripts / "utils").exists()
    assert not (scripts / "audio.py").exists()
    for module in sorted(expected - {"artifacts.py", "config.py", "matching.py"}):
        completed = subprocess.run(
            [sys.executable, str(scripts / module), "--help"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
