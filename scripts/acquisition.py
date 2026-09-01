"""Preview or acquire missing tracks through slskd and verify new files."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import time
from pathlib import Path

import requests
from artifacts import (
    fingerprint_artifact,
    fingerprint_data,
    now_iso,
    read_artifact,
    write_artifact,
)
from catalog import canonical_key, load_catalog, resolve_bpm_master, resolve_destination
from config import SUPPORTED_AUDIO_EXTENSIONS, Config, load_config
from local_library import promote_verified_file
from matching import (
    artist_present_in_filename,
    filename_matches,
    metadata_match_scores,
    parse_filename,
    version_tokens,
)
from mutagen import File as MutagenFile


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _inspect_audio(path: Path) -> dict:
    result = {
        "file_name": path.name,
        "size": path.stat().st_size,
        "sha256": _sha256_file(path),
        "codec": path.suffix.casefold().lstrip("."),
        "duration_ms": None,
        "bitrate_kbps": None,
        "tags": {},
    }
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:  # noqa: BLE001 - malformed audio must become a safe inspection failure
        audio = None
    if audio is None:
        return result
    info = getattr(audio, "info", None)
    if getattr(info, "length", None):
        result["duration_ms"] = round(float(info.length) * 1000)
    if getattr(info, "bitrate", None):
        result["bitrate_kbps"] = round(float(info.bitrate) / 1000)
    tags = getattr(audio, "tags", None) or {}
    for key in ("artist", "title", "album", "isrc"):
        value = tags.get(key)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        if value:
            result["tags"][key] = str(value)
    return result


def _analyze_new_file(path: Path) -> dict:  # pragma: no cover - real audio integration
    try:
        import essentia.standard as essentia
    except ImportError as error:
        raise RuntimeError(
            "Essentia is required to analyze newly acquired files"
        ) from error
    samples = essentia.MonoLoader(filename=str(path.resolve()), sampleRate=44100)()
    rhythm = essentia.RhythmExtractor2013(
        method="multifeature", minTempo=60, maxTempo=220
    )
    bpm, ticks, confidence, estimates, _intervals = rhythm(samples)
    key, scale, key_strength = essentia.KeyExtractor(profileType="edma")(samples)
    return {
        "raw_bpm": round(float(bpm), 4),
        "bpm_estimates": [round(float(value), 4) for value in list(estimates)[:12]],
        "bpm_confidence": round(float(confidence), 6),
        "beat_count": len(ticks),
        "key": str(key),
        "scale": str(scale),
        "key_strength": round(float(key_strength), 6),
    }


def _check_slskd(config: Config) -> None:
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


def _search_slskd(
    config: Config, artist: str, title: str, timeout: int = 30
) -> list[dict]:
    headers = {
        "X-API-Key": config.slskd_api_key or "",
        "Content-Type": "application/json",
    }
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
            f"{config.slskd_base_url}/api/v0/searches/{search_id}",
            headers=headers,
            timeout=5,
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


def _rank_candidates(
    results: list[dict], track: dict, config: Config, limit: int = 5
) -> list[dict]:
    ranked: list[tuple[int, int, int, dict]] = []
    expected_versions = version_tokens(track.get("title", ""))
    for response in results:
        for item in response.get("files", []):
            filename = item.get("filename", "")
            extension = (
                Path(filename).suffix.lstrip(".") or item.get("extension", "")
            ).casefold()
            if extension not in config.preferred_formats:
                continue
            size, duration = int(item.get("size") or 0), int(item.get("length") or 0)
            bitrate = int((size * 8) / (duration * 1000)) if duration else 0
            if extension == "mp3" and bitrate < config.min_bitrate_kbps:
                continue
            if not filename_matches(
                filename, track.get("artist", ""), track.get("title", "")
            ):
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


def _download_candidate(
    config: Config, candidate: dict, intake: Path, timeout: int = 600
) -> Path | None:
    headers = {
        "X-API-Key": config.slskd_api_key or "",
        "Content-Type": "application/json",
    }
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
            if path.is_file()
            and path.stat().st_size
            and path.stat().st_mtime >= requested_at - 2
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


def _normalized_bpm_analysis(analysis: dict, schema: dict) -> dict:
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
            master = resolve_bpm_master(schema, value)
            if master and not any(abs(item["bpm"] - value) <= 1 for item in candidates):
                candidates.append(
                    {"bpm": round(value, 4), "master_folder": master["name"]}
                )
    masters = {item["master_folder"] for item in candidates[:3]}
    return {
        **analysis,
        "bpm_candidates": candidates,
        "selected_bpm": candidates[0]["bpm"] if candidates else None,
        "selected_master_folder": candidates[0]["master_folder"]
        if candidates
        else None,
        "bpm_ambiguous": not candidates or len(masters) > 1,
    }


def _verification_flags(
    track: dict, path: Path, metadata: dict, analysis: dict
) -> list[str]:
    tags = metadata.get("tags", {})
    fallback_artist, fallback_title = parse_filename(path.stem)
    actual_artist = tags.get("artist") or fallback_artist
    actual_title = tags.get("title") or fallback_title
    artist_score, title_score = metadata_match_scores(
        actual_artist, actual_title, track.get("artist", ""), track.get("title", "")
    )
    flags: list[str] = []
    if not (
        (artist_score >= 0.80 and title_score >= 0.85)
        or (
            title_score >= 0.95
            and artist_present_in_filename(track.get("artist", ""), path.stem)
        )
    ):
        flags.append("artist-title-mismatch")
    expected_duration, actual_duration = (
        track.get("duration_ms"),
        metadata.get("duration_ms"),
    )
    if not expected_duration or not actual_duration:
        flags.append("duration-unavailable")
    elif abs(int(expected_duration) - int(actual_duration)) > 10_000:
        flags.append("duration-mismatch")
    expected_versions = version_tokens(track.get("title", ""))
    actual_versions = version_tokens(f"{actual_title} {path.stem}")
    if not expected_versions.issubset(
        actual_versions
    ) or actual_versions - expected_versions - {"mix"}:
        flags.append("version-mismatch")
    if analysis.get("bpm_ambiguous"):
        flags.append("ambiguous-bpm")
    elif canonical_key(analysis.get("selected_master_folder", "")) != canonical_key(
        track.get("master_folder", "")
    ):
        flags.append("bpm-band-mismatch")
    return flags


def _existing_intake_file(intake: Path) -> Path | None:
    matches = (
        [
            path
            for path in intake.iterdir()
            if path.is_file() and path.suffix.casefold() in SUPPORTED_AUDIO_EXTENSIONS
        ]
        if intake.is_dir()
        else []
    )
    if len(matches) > 1:
        raise ValueError(f"Multiple intake files require review: {intake}")
    return matches[0] if matches else None


def apply_acquisition_plan(
    plan: dict,
    config: Config,
    schema: dict,
    search=_search_slskd,
    download=_download_candidate,
    analyze=_analyze_new_file,
) -> dict:
    results: list[dict] = []
    review: list[dict] = []
    tracks = plan.get("actions", [])
    for index, track in enumerate(tracks, 1):
        spotify_id = track.get("spotify_track_id")
        print(f"Acquiring {index}/{len(tracks)}: {track['artist']} — {track['title']}")
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
            existing = _existing_intake_file(intake)
        except ValueError:
            existing = intake
        if existing is not None:
            item = {
                **track,
                "status": "review-required",
                "intake_path": _relative_path(intake, config.project_root),
                "review_flags": ["intake-collision"],
            }
            results.append(item)
            review.append(item)
            continue
        try:
            candidates = _rank_candidates(
                search(config, track.get("artist", ""), track.get("title", "")),
                track,
                config,
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
            item = {
                **track,
                "status": "download-failed",
                "review_flags": ["no-candidate-downloaded"],
            }
            results.append(item)
            review.append(item)
            continue
        try:
            metadata = _inspect_audio(acquired)
            analysis = _normalized_bpm_analysis(analyze(acquired), schema)
        except Exception as error:  # noqa: BLE001 - analysis failures quarantine the download
            item = {
                **track,
                "status": "review-required",
                "intake_path": _relative_path(acquired, config.project_root),
                "review_flags": ["audio-analysis-failed"],
                "error": str(error),
            }
            results.append(item)
            review.append(item)
            continue
        flags = _verification_flags(track, acquired, metadata, analysis)
        item = {
            **track,
            "acquisition_source": source,
            "intake_path": _relative_path(acquired, config.project_root),
            "file": metadata,
            "analysis": analysis,
            "review_flags": flags,
        }
        if flags:
            item["status"] = "review-required"
            review.append(item)
            results.append(item)
            continue
        destination_folder = config.project_root / track["local_folder"]
        try:
            promote_verified_file(acquired, destination_folder, config.project_root)
        except FileExistsError:
            item["status"] = "review-required"
            item["review_flags"] = ["destination-collision"]
            review.append(item)
        else:
            item["status"] = "promoted"
            item["local_path"] = _relative_path(
                destination_folder / acquired.name, config.project_root
            )
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


def build_acquisition_plan(delta: dict, catalog: dict) -> dict:
    actions: list[dict] = []
    for track in delta.get("tracks", []):
        destination = resolve_destination(
            catalog,
            track.get("master_folder", ""),
            track.get("main_genre", ""),
            track.get("subgenre"),
        )
        actions.append(
            {
                **track,
                "plan_flags": []
                if destination
                and destination.get("local_folder") == track.get("local_folder")
                else ["intended-destination-invalid"],
            }
        )
    return {
        "created_at": now_iso(),
        "actions": actions,
        "summary": {"tracks": len(actions)},
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delta", type=Path, default=Path("data/library_delta.json"))
    parser.add_argument("--plan", type=Path, default=Path("data/acquisition_plan.json"))
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    config = load_config()
    catalog = load_catalog(config.schema_path)
    if not args.apply:
        delta = read_artifact(args.delta, "library-delta")
        preview = build_acquisition_plan(delta, catalog)
        preview["inputs"] = {
            "delta": fingerprint_artifact(args.delta),
            "catalog": fingerprint_data(catalog),
        }
        preview["input_paths"] = {"delta": str(args.delta.resolve())}
        write_artifact(args.plan, "acquisition-plan", preview)
        print(f"Would acquire {len(preview['actions'])} track(s). See {args.plan}")
        return
    plan = read_artifact(args.plan, "acquisition-plan")
    delta_path = Path((plan.get("input_paths") or {})["delta"])
    expected = {
        "delta": fingerprint_artifact(delta_path),
        "catalog": fingerprint_data(catalog),
    }
    if plan.get("inputs") != expected:
        raise ValueError("Acquisition plan is stale; preview it again")
    _check_slskd(config)
    result = apply_acquisition_plan(plan, config, catalog)
    write_artifact(
        config.data_dir / "acquisition_results.json", "acquisition-results", result
    )
    write_artifact(
        config.data_dir / "intake_review.json",
        "intake-review",
        {"tracks": result["review"], "summary": {"review": len(result["review"])}},
    )
    print(
        f"Promoted {result['summary']['promoted']} track(s); review {result['summary']['review']}."
    )


if __name__ == "__main__":
    main()
