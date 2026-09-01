"""Versioned work-artifact persistence, timestamps, and fingerprints."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


ARTIFACT_VERSION = 1


def read_artifact(path: Path, expected_kind: str | None = None) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"Expected a JSON object in {path}")
    if expected_kind is not None:
        metadata = data.get("_artifact") or {}
        if metadata.get("kind") != expected_kind:
            raise ValueError(
                f"Expected {expected_kind!r} artifact in {path}; found {metadata.get('kind')!r}"
            )
        if metadata.get("version") != ARTIFACT_VERSION:
            raise ValueError(
                f"Unsupported {expected_kind!r} artifact version in {path}: "
                f"{metadata.get('version')!r}"
            )
    return data


def write_artifact(path: Path, kind: str, data: Any) -> Path:
    """Write a versioned JSON artifact atomically."""

    if not isinstance(data, dict):
        raise TypeError("Artifact payload must be a JSON object")
    payload = dict(data)
    payload["_artifact"] = {
        "kind": kind,
        "version": ARTIFACT_VERSION,
        "created_at": (data.get("_artifact") or {}).get("created_at") or now_iso(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return path


def write_text(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)
    return path


def fingerprint_data(data: Any) -> str:
    encoded = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def fingerprint_artifact(path: Path) -> str:
    return fingerprint_data(read_artifact(path))


def validate_plan(
    plan: dict, expected_kind: str, expected_inputs: dict[str, str]
) -> None:
    metadata = plan.get("_artifact") or {}
    if (
        metadata.get("kind") != expected_kind
        or metadata.get("version") != ARTIFACT_VERSION
    ):
        raise ValueError(f"Expected current {expected_kind!r} plan")
    if (plan.get("inputs") or {}) != expected_inputs:
        raise ValueError("Plan inputs are stale; preview the operation again")
