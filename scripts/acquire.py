#!/usr/bin/env python3
"""Preview or acquire missing tracks through slskd and verify new files."""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

import requests

from utils.audio import AUDIO_EXTENSIONS, analyze_new_file, inspect_audio
from utils.config import Config, load_config
from utils.files import apply_move_plan, build_move_plan
from utils.io import now_iso, read_json, relative_path, write_json
from utils.matching import artist_found_in_stem, filename_matches, match_scores, preprocess_stem
from utils.schema import bpm_master, canonical_key, load_schema, resolve_destination

VERSION_WORDS = {"remix", "edit", "extended", "radio", "instrumental", "live", "sped up", "slowed"}


def check_slskd(config: Config) -> None:
    """Require the user-managed slskd service to already be available."""

    try:
        response = requests.get(
            f"{config.slskd_base_url}/api/v0/session",
            headers={"X-API-Key": config.slskd_api_key or ""},
            timeout=3,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise RuntimeError(
            f"slskd is unavailable at {config.slskd_base_url}. Start it yourself and rerun --apply."
        ) from error


def search_slskd(config: Config, artist: str, title: str, timeout: int = 30) -> list[dict]:
    headers = {"X-API-Key": config.slskd_api_key or "", "Content-Type": "application/json"}
    response = requests.post(
        f"{config.slskd_base_url}/api/v0/searches",
        headers=headers,
        json={"searchText": f"{artist} {title}", "fileLimit": 200},
        timeout=10,
    )
    response.raise_for_status()
    search_id = response.json()["id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = requests.get(
            f"{config.slskd_base_url}/api/v0/searches/{search_id}", headers=headers, timeout=5
        )
        status.raise_for_status()
        if status.json().get("isComplete"):
            break
        time.sleep(2)
    result = requests.get(
        f"{config.slskd_base_url}/api/v0/searches/{search_id}/responses",
        headers=headers,
        timeout=5,
    )
    result.raise_for_status()
    payload = result.json()
    return payload if isinstance(payload, list) else []


def rank_candidates(results: list[dict], track: dict, config: Config, limit: int = 5) -> list[dict]:
    ranked: list[tuple[int, int, int, dict]] = []
    expected_versions = version_tokens(track.get("title", ""))
    for response in results:
        for item in response.get("files", []):
            filename = item.get("filename", "")
            extension = (Path(filename).suffix.lstrip(".") or item.get("extension", "")).casefold()
            if extension not in config.preferred_formats:
                continue
            size, duration = int(item.get("size") or 0), int(item.get("length") or 0)
            bitrate = int((size * 8) / (duration * 1000)) if duration else 0
            if extension == "mp3" and bitrate < config.min_bitrate_kbps:
                continue
            if not filename_matches(filename, track.get("artist", ""), track.get("title", "")):
                continue
            candidate = {
                "username": response.get("username", ""),
                "filename": filename,
                "size": size,
                "format": extension,
                "estimated_bitrate_kbps": bitrate,
            }
            ranked.append(
                (
                    config.preferred_formats.index(extension),
                    0 if version_tokens(filename) == expected_versions else 1,
                    -size,
                    candidate,
                )
            )
    return [candidate for *_score, candidate in sorted(ranked)[:limit]]


def download_candidate(
    config: Config, candidate: dict, intake: Path, timeout: int = 600
) -> Path | None:
    headers = {"X-API-Key": config.slskd_api_key or "", "Content-Type": "application/json"}
    requested_at = time.time()
    response = requests.post(
        f"{config.slskd_base_url}/api/v0/transfers/downloads/{candidate['username']}",
        headers=headers,
        json=[{"filename": candidate["filename"], "size": candidate["size"]}],
        timeout=10,
    )
    response.raise_for_status()
    file_name = candidate["filename"].replace("\\", "/").rsplit("/", 1)[-1]
    deadline = time.time() + timeout
    while time.time() < deadline:
        matches = [
            path
            for path in config.slskd_downloads_dir.rglob(file_name)
            if path.is_file() and path.stat().st_size and path.stat().st_mtime >= requested_at - 2
        ]
        if matches:
            intake.mkdir(parents=True, exist_ok=True)
            target = intake / file_name
            if target.exists():
                raise FileExistsError(f"Intake file already exists: {target}")
            shutil.move(str(matches[0]), str(target))
            return target
        time.sleep(4)
    return None


def normalized_bpm_analysis(analysis: dict, schema: dict) -> dict:
    values = [analysis.get("raw_bpm"), *analysis.get("bpm_estimates", [])]
    candidates: list[dict] = []
    for raw in values:
        if not raw:
            continue
        family = [float(raw)]
        if float(raw) < 118:
            family.append(float(raw) * 2)
        if float(raw) > 180:
            family.append(float(raw) / 2)
        for value in family:
            master = bpm_master(schema, value)
            if master and not any(abs(item["bpm"] - value) <= 1 for item in candidates):
                candidates.append({"bpm": round(value, 4), "master_folder": master["name"]})
    masters = {item["master_folder"] for item in candidates[:3]}
    return {
        **analysis,
        "bpm_candidates": candidates,
        "selected_bpm": candidates[0]["bpm"] if candidates else None,
        "selected_master_folder": candidates[0]["master_folder"] if candidates else None,
        "bpm_ambiguous": not candidates or len(masters) > 1,
    }


def version_tokens(value: str) -> set[str]:
    normalized = f" {canonical_key(value)} "
    return {word for word in VERSION_WORDS if f" {canonical_key(word)} " in normalized}


def verification_flags(track: dict, path: Path, metadata: dict, analysis: dict) -> list[str]:
    tags = metadata.get("tags", {})
    fallback_artist, fallback_title = preprocess_stem(path.stem)
    actual_artist = tags.get("artist") or fallback_artist
    actual_title = tags.get("title") or fallback_title
    artist_score, title_score = match_scores(
        actual_artist, actual_title, track.get("artist", ""), track.get("title", "")
    )
    flags: list[str] = []
    if not (
        (artist_score >= 0.80 and title_score >= 0.85)
        or (title_score >= 0.95 and artist_found_in_stem(track.get("artist", ""), path.stem))
    ):
        flags.append("artist-title-mismatch")
    expected_duration, actual_duration = track.get("duration_ms"), metadata.get("duration_ms")
    if not expected_duration or not actual_duration:
        flags.append("duration-unavailable")
    elif abs(int(expected_duration) - int(actual_duration)) > 10_000:
        flags.append("duration-mismatch")
    expected_versions = version_tokens(track.get("title", ""))
    actual_versions = version_tokens(f"{actual_title} {path.stem}")
    if not expected_versions.issubset(actual_versions) or actual_versions - expected_versions - {"mix"}:
        flags.append("version-mismatch")
    if analysis.get("bpm_ambiguous"):
        flags.append("ambiguous-bpm")
    elif canonical_key(analysis.get("selected_master_folder", "")) != canonical_key(
        track.get("master_folder", "")
    ):
        flags.append("bpm-band-mismatch")
    return flags


def existing_intake_file(intake: Path) -> Path | None:
    matches = [
        path for path in intake.iterdir() if path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS
    ] if intake.is_dir() else []
    if len(matches) > 1:
        raise ValueError(f"Multiple intake files require review: {intake}")
    return matches[0] if matches else None


def acquire_tracks(
    delta: dict,
    config: Config,
    schema: dict,
    search=search_slskd,
    download=download_candidate,
    analyze=analyze_new_file,
) -> dict:
    results: list[dict] = []
    review: list[dict] = []
    for index, track in enumerate(delta.get("tracks", []), 1):
        spotify_id = track.get("spotify_track_id")
        print(f"Acquiring {index}/{len(delta.get('tracks', []))}: {track['artist']} — {track['title']}")
        intended = resolve_destination(
            schema,
            track.get("master_folder", ""),
            track.get("main_genre", ""),
            track.get("subgenre"),
        )
        if (
            not spotify_id
            or intended is None
            or intended.get("local_folder") != track.get("local_folder")
        ):
            item = {
                **track,
                "status": "review-required",
                "review_flags": ["intended-destination-invalid"],
            }
            results.append(item)
            review.append(item)
            continue
        intake = config.music_root / "intake" / spotify_id
        try:
            existing = existing_intake_file(intake)
        except ValueError:
            existing = intake
        if existing is not None:
            item = {
                **track,
                "status": "review-required",
                "intake_path": relative_path(intake, config.project_root),
                "review_flags": ["intake-collision"],
            }
            results.append(item)
            review.append(item)
            continue
        try:
            candidates = rank_candidates(
                search(config, track.get("artist", ""), track.get("title", "")), track, config
            )
            acquired = None
            source = {"provider": "slskd"}
            for candidate in candidates:
                acquired = download(config, candidate, intake)
                if acquired:
                    source = {"provider": "slskd", **candidate}
                    break
        except (requests.RequestException, FileExistsError) as error:
            item = {
                **track,
                "status": "review-required",
                "review_flags": ["download-error"],
                "error": str(error),
            }
            results.append(item)
            review.append(item)
            continue
        if acquired is None:
            item = {**track, "status": "download-failed", "review_flags": ["no-candidate-downloaded"]}
            results.append(item)
            review.append(item)
            continue
        try:
            metadata = inspect_audio(acquired)
            analysis = normalized_bpm_analysis(analyze(acquired), schema)
        except Exception as error:
            item = {
                **track,
                "status": "review-required",
                "intake_path": relative_path(acquired, config.project_root),
                "review_flags": ["audio-analysis-failed"],
                "error": str(error),
            }
            results.append(item)
            review.append(item)
            continue
        flags = verification_flags(track, acquired, metadata, analysis)
        item = {
            **track,
            "acquisition_source": source,
            "intake_path": relative_path(acquired, config.project_root),
            "file": metadata,
            "analysis": analysis,
            "review_flags": flags,
        }
        if flags:
            item["status"] = "review-required"
            review.append(item)
            results.append(item)
            continue
        destination = config.project_root / track["local_folder"] / acquired.name
        plan = build_move_plan(
            [{"source": item["intake_path"], "destination": relative_path(destination, config.project_root)}],
            config.project_root,
        )
        if not plan["safe_to_apply"]:
            item["status"] = "review-required"
            item["review_flags"] = ["destination-collision"]
            review.append(item)
        else:
            apply_move_plan(plan, config.project_root)
            item["status"] = "promoted"
            item["local_path"] = relative_path(destination, config.project_root)
        results.append(item)
    return {
        "completed_at": now_iso(),
        "tracks": results,
        "review": review,
        "summary": {
            "tracks": len(results),
            "promoted": sum(item["status"] == "promoted" for item in results),
            "review": len(review),
        },
    }


def build_preview(delta: dict) -> dict:
    return {
        "created_at": now_iso(),
        "tracks": [
            {
                "spotify_track_id": track.get("spotify_track_id"),
                "artist": track.get("artist"),
                "title": track.get("title"),
                "intended_local_folder": track.get("local_folder"),
            }
            for track in delta.get("tracks", [])
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("delta", type=Path, nargs="?", default=Path("data/delta.json"))
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    delta = read_json(args.delta)
    if not args.apply:
        preview = build_preview(delta)
        write_json(config.data_dir / "acquisition_plan.json", preview)
        print(f"Would acquire {len(preview['tracks'])} track(s). See data/acquisition_plan.json")
        return
    check_slskd(config)
    result = acquire_tracks(delta, config, load_schema(config.schema_path))
    write_json(config.data_dir / "acquisition_results.json", result)
    write_json(
        config.data_dir / "intake_review.json",
        {"tracks": result["review"], "summary": {"review": len(result["review"])}},
    )
    print(f"Promoted {result['summary']['promoted']} track(s); review {result['summary']['review']}.")


if __name__ == "__main__":
    main()
