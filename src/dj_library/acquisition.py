"""Explicitly planned acquisition into neutral, run-specific intake folders."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import requests

from .artifacts import now_iso, relative_to_root, seal_manifest, verify_manifest
from .audio import AUDIO_EXTENSIONS, inspect_audio
from .config import Settings
from .matching import filename_matches


def rank_candidates(
    results: list,
    *,
    min_bitrate_kbps: int,
    preferred_formats: tuple[str, ...],
    artist: str,
    title: str,
    limit: int = 5,
) -> list[dict]:
    """Rank exact-enough Soulseek candidates by format, version, and size."""

    candidates: list[tuple[int, int, int, dict]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        username = result.get("username", "")
        for item in result.get("files", []):
            if not isinstance(item, dict):
                continue
            filename = item.get("filename", "")
            extension = (Path(filename).suffix.lstrip(".") or item.get("extension", "")).casefold()
            if extension not in preferred_formats:
                continue
            size = int(item.get("size") or 0)
            duration = int(item.get("length") or 0)
            bitrate = int((size * 8) / (duration * 1000)) if duration else 0
            if extension == "mp3" and bitrate < min_bitrate_kbps:
                continue
            if not filename_matches(filename, artist, title):
                continue
            value = {
                "username": username,
                "filename": filename,
                "size": size,
                "estimated_bitrate_kbps": bitrate,
                "format": extension,
            }
            candidates.append(
                (
                    preferred_formats.index(extension),
                    0 if "extended" in filename.casefold() else 1,
                    -size,
                    value,
                )
            )
    return [item for *_score, item in sorted(candidates)[:limit]]


def build_acquisition_plan(delta: dict, *, settings: Settings, run_id: str) -> dict:
    """Freeze the exact requested tracks and neutral intake destination."""

    intake = settings.music_root / "intake" / run_id
    tracks = [
        {
            **track,
            "spotify_id": track.get("spotify_id") or track.get("spotify_track_id"),
            "intended_local_folder": track["local_folder"],
        }
        for track in delta.get("tracks", [])
    ]
    return seal_manifest(
        {
            "artifact_type": "acquisition_plan",
            "artifact_version": 1,
            "status": "ready_for_review",
            "created_at": now_iso(),
            "run_id": run_id,
            "intake_folder": relative_to_root(intake, settings),
            "tracks": tracks,
            "quality": {
                "minimum_mp3_bitrate_kbps": settings.min_bitrate_kbps,
                "preferred_formats": list(settings.preferred_formats),
            },
            "summary": {"tracks": len(tracks)},
        }
    )


def _existing_intake_matches(intake: Path, artist: str, title: str) -> list[Path]:
    if not intake.is_dir():
        return []
    return sorted(
        path
        for path in intake.iterdir()
        if path.is_file()
        and path.suffix.casefold() in AUDIO_EXTENSIONS
        and filename_matches(path.name, artist, title)
    )


def ensure_slskd(settings: Settings, *, retries: int = 15) -> None:  # pragma: no cover
    """Ensure a local slskd API is ready during an explicitly applied acquisition."""

    headers = {"X-API-Key": settings.slskd_api_key or ""}
    url = f"{settings.slskd_base_url}/api/v0/session"
    try:
        response = requests.get(url, headers=headers, timeout=3)
        response.raise_for_status()
        return
    except requests.RequestException as initial_error:
        host = (urlparse(settings.slskd_base_url).hostname or "").casefold()
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError(f"slskd is unavailable: {initial_error}") from initial_error
    result = subprocess.run(
        ["docker", "start", settings.slskd_container_name],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Docker error"
        raise RuntimeError(f"Unable to start slskd: {detail}")
    for _ in range(retries):
        try:
            response = requests.get(url, headers=headers, timeout=3)
            response.raise_for_status()
            return
        except requests.RequestException:
            time.sleep(1)
    raise RuntimeError("slskd did not become ready")


def search_slskd(  # pragma: no cover
    settings: Settings, query: str, *, timeout_seconds: int = 30
) -> list:
    headers = {"X-API-Key": settings.slskd_api_key or "", "Content-Type": "application/json"}
    response = requests.post(
        f"{settings.slskd_base_url}/api/v0/searches",
        headers=headers,
        json={"searchText": query, "fileLimit": 200},
        timeout=10,
    )
    response.raise_for_status()
    search_id = response.json()["id"]
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status = requests.get(
            f"{settings.slskd_base_url}/api/v0/searches/{search_id}",
            headers=headers,
            timeout=5,
        ).json()
        if status.get("isComplete"):
            break
        time.sleep(2)
    responses = requests.get(
        f"{settings.slskd_base_url}/api/v0/searches/{search_id}/responses",
        headers=headers,
        timeout=5,
    )
    if not responses.ok:
        return []
    data = responses.json()
    return data if isinstance(data, list) else []


def download_candidate(
    settings: Settings,
    candidate: dict,
    intake: Path,
    *,
    timeout_seconds: int = 600,
) -> Path | None:  # pragma: no cover
    """Queue one exact candidate and return its completed neutral-intake path."""

    headers = {"X-API-Key": settings.slskd_api_key or "", "Content-Type": "application/json"}
    response = requests.post(
        f"{settings.slskd_base_url}/api/v0/transfers/downloads/{candidate['username']}",
        headers=headers,
        json=[{"filename": candidate["filename"], "size": candidate["size"]}],
        timeout=10,
    )
    response.raise_for_status()
    file_name = candidate["filename"].replace("\\", "/").rsplit("/", 1)[-1]
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for path in settings.slskd_downloads_dir.rglob(file_name):
            if path.is_file() and path.stat().st_size:
                intake.mkdir(parents=True, exist_ok=True)
                target = intake / file_name
                if target.exists():
                    raise FileExistsError(f"Intake collision: {target}")
                path.rename(target)
                return target
        time.sleep(4)
    return None


def apply_acquisition_plan(
    plan: dict,
    *,
    settings: Settings,
    search: Callable[[Settings, str], list] = search_slskd,
    download: Callable[[Settings, dict, Path], Path | None] = download_candidate,
    ensure_ready: Callable[[Settings], None] = ensure_slskd,
) -> dict:
    """Execute only the reviewed track set and record every acquisition outcome."""

    verify_manifest(plan, artifact_type="acquisition_plan")
    if plan.get("status") != "ready_for_review":
        raise ValueError("Acquisition plan is not ready to apply")
    ensure_ready(settings)
    intake = settings.project_root / plan["intake_folder"]
    records: list[dict] = []
    for track in plan.get("tracks", []):
        existing = _existing_intake_matches(
            intake,
            track.get("artist", ""),
            track.get("title", ""),
        )
        if len(existing) > 1:
            records.append(
                {
                    **track,
                    "status": "failed",
                    "error": "ambiguous-existing-intake-files",
                    "existing_files": [relative_to_root(path, settings) for path in existing],
                    "acquired_file": None,
                }
            )
            continue
        if existing:
            metadata = inspect_audio(existing[0])
            metadata["intake_path"] = relative_to_root(existing[0], settings)
            records.append(
                {
                    **track,
                    "status": "downloaded_pending_analysis",
                    "acquisition_source": {"provider": "existing-intake"},
                    "acquired_file": metadata,
                }
            )
            continue
        candidates = rank_candidates(
            search(settings, f"{track.get('artist', '')} {track.get('title', '')}"),
            min_bitrate_kbps=settings.min_bitrate_kbps,
            preferred_formats=settings.preferred_formats,
            artist=track.get("artist", ""),
            title=track.get("title", ""),
        )
        acquired: Path | None = None
        selected: dict | None = None
        for candidate in candidates:
            acquired = download(settings, candidate, intake)
            if acquired:
                selected = candidate
                break
        if not acquired:
            records.append({**track, "status": "failed", "acquired_file": None})
            continue
        metadata = inspect_audio(acquired)
        metadata["intake_path"] = relative_to_root(acquired, settings)
        records.append(
            {
                **track,
                "status": "downloaded_pending_analysis",
                "acquisition_source": {"provider": "slskd", **(selected or {})},
                "acquired_file": metadata,
            }
        )
    return seal_manifest(
        {
            "artifact_type": "acquisition_manifest",
            "artifact_version": 2,
            "run_id": plan["run_id"],
            "created_at": now_iso(),
            "source_plan_sha256": plan["manifest_sha256"],
            "intake_folder": plan["intake_folder"],
            "tracks": records,
            "summary": {
                "downloaded": sum(
                    item["status"] == "downloaded_pending_analysis" for item in records
                ),
                "failed": sum(item["status"] == "failed" for item in records),
            },
        }
    )
