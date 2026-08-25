from __future__ import annotations

import json

import pytest
from tests.helpers import settings_for

from dj_library.artifacts import (
    canonical_json,
    create_run_dir,
    error_result,
    payload_hash,
    read_json,
    seal_manifest,
    sha256_bytes,
    sha256_file,
    tool_result,
    verify_manifest,
    write_json,
)


def test_canonical_json_is_order_independent():
    assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})


def test_sha256_helpers(tmp_path):
    path = tmp_path / "value.bin"
    path.write_bytes(b"abc")
    assert sha256_file(path) == sha256_bytes(b"abc")


def test_seal_and_verify_manifest():
    manifest = seal_manifest({"artifact_type": "demo", "value": 1})
    verify_manifest(manifest, artifact_type="demo")
    assert manifest["manifest_sha256"] == payload_hash(manifest)


@pytest.mark.parametrize("change", [{"value": 2}, {"manifest_sha256": "bad"}])
def test_verify_manifest_rejects_changes(change):
    manifest = seal_manifest({"artifact_type": "demo", "value": 1})
    manifest.update(change)
    with pytest.raises(ValueError, match="hash"):
        verify_manifest(manifest)


def test_verify_manifest_rejects_wrong_type():
    manifest = seal_manifest({"artifact_type": "demo"})
    with pytest.raises(ValueError, match="Expected"):
        verify_manifest(manifest, artifact_type="other")


def test_json_roundtrip_is_utf8_and_atomic(tmp_path):
    path = tmp_path / "nested/artifact.json"
    write_json(path, {"artist": "Tiësto"})
    assert read_json(path) == {"artist": "Tiësto"}
    assert not list(path.parent.glob("*.tmp"))


def test_read_json_requires_object(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="object"):
        read_json(path)


def test_create_run_dir_never_reuses(tmp_path):
    settings = settings_for(tmp_path)
    path = create_run_dir(settings, "sync", "run")
    assert path.is_dir()
    with pytest.raises(FileExistsError):
        create_run_dir(settings, "sync", "run")


def test_tool_envelopes_are_stable():
    assert tool_result("demo", summary={"count": 1}) == {
        "ok": True,
        "action": "demo",
        "run_id": None,
        "artifacts": {},
        "summary": {"count": 1},
    }
    failure = error_result("demo", RuntimeError("boom"))
    assert failure["ok"] is False
    assert failure["error"] == {"type": "RuntimeError", "message": "boom"}


def test_write_json_emits_valid_json(tmp_path):
    path = write_json(tmp_path / "a.json", {"b": [1, 2]})
    assert json.loads(path.read_text()) == {"b": [1, 2]}
