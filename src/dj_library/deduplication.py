"""Recoverable duplicate detection that quarantines rather than deletes audio."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile

from .artifacts import now_iso, relative_to_root, seal_manifest, sha256_file
from .audio import AUDIO_EXTENSIONS
from .config import Settings
from .matching import preprocess_stem
from .reconciliation import apply_move_manifest, build_move_manifest

LOSSLESS_EXTENSIONS = {".flac", ".wav", ".aiff", ".aif"}
_DUPLICATE_SUFFIX = re.compile(r"\s*\[duplicate-[0-9a-f]+\]\s*$", re.IGNORECASE)


def normalize_identity(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    value = re.sub(r"\[duplicate-[0-9a-f]+\]", "", value.casefold().replace("_", " "))
    value = re.sub(r"\bspoti\s*downloader(?:\.com)?\b|\bwww\.groovytunes\.org\b", "", value)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def normalize_title(value: str) -> str:
    value = re.sub(r"(?:\(|\[)?\boriginal[_ ]mix\b(?:\)|\])?", " ", value, flags=re.I)
    return normalize_identity(value)


def _tag(tags: Any, key: str) -> str:
    value = tags.get(key) if tags else None
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def inspect_candidate(path: Path, settings: Settings) -> dict[str, Any]:
    audio = MutagenFile(path, easy=True)
    if audio is None or not getattr(audio, "info", None):
        raise ValueError(f"Unreadable audio metadata: {relative_to_root(path, settings)}")
    tags = audio.tags or {}
    artist = _tag(tags, "artist")
    title = _tag(tags, "title")
    if not title:
        artist, title = preprocess_stem(path.stem)
    info = audio.info
    return {
        "path": relative_to_root(path, settings),
        "filename": path.name,
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "extension": path.suffix.casefold(),
        "duration_ms": round(float(info.length) * 1000),
        "artist": artist,
        "title": title,
        "identity": [normalize_identity(artist), normalize_title(title)],
        "isrc": normalize_identity(_tag(tags, "isrc")).replace(" ", "").upper() or None,
        "bitrate": int(getattr(info, "bitrate", 0) or 0),
        "sample_rate": int(getattr(info, "sample_rate", 0) or 0),
        "bits_per_sample": int(getattr(info, "bits_per_sample", 0) or 0),
        "channels": int(getattr(info, "channels", 0) or 0),
        "tag_count": sum(
            bool(_tag(tags, key)) for key in ("artist", "title", "album", "genre", "bpm", "isrc")
        ),
        "explicit_duplicate_suffix": bool(_DUPLICATE_SUFFIX.search(path.stem)),
        "source_penalty": int("spotidownloader" in normalize_identity(path.name).replace(" ", "")),
    }


def quality_key(item: dict[str, Any]) -> tuple[Any, ...]:
    lossless = item["extension"] in LOSSLESS_EXTENSIONS
    container = {".flac": 4, ".wav": 3, ".aiff": 3, ".aif": 3, ".m4a": 2, ".ogg": 1, ".mp3": 0}
    return (
        int(lossless),
        item["bits_per_sample"],
        item["sample_rate"],
        item["bitrate"] if not lossless else 0,
        item["channels"],
        -item["source_penalty"],
        item["tag_count"],
        int(bool(item["isrc"])),
        int(not item["explicit_duplicate_suffix"]),
        container.get(item["extension"], -1),
        item["path"],
    )


def find_duplicate_groups(files: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Return strong duplicate groups and ambiguous same-identity groups."""

    by_identity: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in files:
        identity = tuple(item["identity"])
        if all(identity):
            by_identity[identity].append(item)
    duplicates: list[dict] = []
    uncertain: list[dict] = []
    for identity, items in by_identity.items():
        if len(items) < 2:
            continue
        durations = [item["duration_ms"] for item in items]
        if max(durations) - min(durations) > 3000:
            uncertain.append(
                {"identity": list(identity), "files": items, "reason": "duration_spread"}
            )
            continue
        ranked = sorted(items, key=quality_key, reverse=True)
        duplicates.append({"identity": list(identity), "keep": ranked[0], "remove": ranked[1:]})
    return duplicates, uncertain


def build_deduplication_manifest(settings: Settings, *, quarantine_root: Path) -> dict:
    paths = sorted(
        path
        for path in settings.music_root.rglob("*")
        if path.is_file()
        and path.suffix.casefold() in AUDIO_EXTENSIONS
        and "review" not in path.relative_to(settings.music_root).parts
    )
    files: list[dict] = []
    unreadable: list[dict] = []
    for path in paths:
        try:
            files.append(inspect_candidate(path, settings))
        except Exception as error:
            unreadable.append({"path": relative_to_root(path, settings), "error": str(error)})
    groups, uncertain = find_duplicate_groups(files)
    moves = []
    music_relative = settings.music_root.relative_to(settings.project_root)
    for group in groups:
        for item in group["remove"]:
            source_relative = Path(item["path"])
            try:
                library_relative = source_relative.relative_to(music_relative)
            except ValueError:
                library_relative = source_relative
            moves.append(
                {
                    "source": item["path"],
                    "destination": relative_to_root(
                        quarantine_root / library_relative,
                        settings,
                    ),
                }
            )
    move_manifest = build_move_manifest(
        moves, settings=settings, purpose="local-audio-deduplication"
    )
    manifest = {
        "artifact_type": "local_audio_deduplication_manifest",
        "artifact_version": 2,
        "status": move_manifest["status"],
        "created_at": now_iso(),
        "groups": groups,
        "uncertain_groups_preserved": uncertain,
        "unreadable_files_preserved": unreadable,
        "move_manifest": move_manifest,
        "summary": {
            "files_scanned": len(paths),
            "duplicate_groups": len(groups),
            "files_to_quarantine": len(moves),
            "uncertain_groups": len(uncertain),
        },
    }
    return seal_manifest(manifest)


def apply_deduplication_manifest(manifest: dict, *, settings: Settings) -> dict:
    from .artifacts import verify_manifest

    verify_manifest(manifest, artifact_type="local_audio_deduplication_manifest")
    result = apply_move_manifest(manifest["move_manifest"], settings=settings)
    return {
        "artifact_type": "local_audio_deduplication_apply_result",
        "artifact_version": 2,
        "created_at": now_iso(),
        "manifest_sha256": manifest["manifest_sha256"],
        "move_result": result,
        "summary": result["summary"],
    }
