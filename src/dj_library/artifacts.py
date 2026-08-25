"""Canonical JSON, hashing, run directories, and tool-result helpers."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings


def now_iso() -> str:
    """Return an audit-friendly UTC timestamp."""

    return datetime.now(UTC).isoformat()


def new_run_id() -> str:
    """Return a sortable UTC run identifier with collision-resistant precision."""

    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def canonical_json(data: Any) -> bytes:
    """Encode JSON deterministically for manifest hashing."""

    return json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_hash(data: dict, hash_key: str = "manifest_sha256") -> str:
    """Hash a manifest while excluding its self-referential hash field."""

    return sha256_bytes(
        canonical_json({key: value for key, value in data.items() if key != hash_key})
    )


def seal_manifest(data: dict) -> dict:
    sealed = dict(data)
    sealed["manifest_sha256"] = payload_hash(sealed)
    return sealed


def verify_manifest(data: dict, *, artifact_type: str | None = None) -> None:
    """Validate a sealed manifest before any consequential mutation."""

    if artifact_type and data.get("artifact_type") != artifact_type:
        raise ValueError(f"Expected {artifact_type!r}, got {data.get('artifact_type')!r}")
    expected = data.get("manifest_sha256")
    if not expected or expected != payload_hash(data):
        raise ValueError("Manifest hash does not match its contents")


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def write_json(path: Path, data: Any) -> Path:
    """Write formatted JSON atomically and return the destination path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def create_run_dir(settings: Settings, domain: str, run_id: str | None = None) -> Path:
    """Create a new immutable run directory; never reuse an existing run."""

    destination = settings.runs_root / domain / (run_id or new_run_id())
    destination.mkdir(parents=True, exist_ok=False)
    return destination


def relative_to_root(path: Path, settings: Settings) -> str:
    return str(path.resolve().relative_to(settings.project_root))


def tool_result(
    action: str,
    *,
    run_id: str | None = None,
    artifacts: dict[str, str] | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable response envelope emitted by every command adapter."""

    return {
        "ok": True,
        "action": action,
        "run_id": run_id,
        "artifacts": artifacts or {},
        "summary": summary or {},
    }


def error_result(action: str, error: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "action": action,
        "run_id": None,
        "artifacts": {},
        "summary": {},
        "error": {"type": type(error).__name__, "message": str(error)},
    }
