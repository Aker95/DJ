"""Read-only audio metadata, hashing, and pre-Rekordbox BPM analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile

from .artifacts import sha256_file
from .schema import bpm_master

AUDIO_EXTENSIONS = {
    ".aac",
    ".aif",
    ".aiff",
    ".alac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
}


def normalize_bpm_candidates(raw: list[dict], schema: dict) -> list[dict]:
    """Normalize defensible half/double-time values into configured BPM bands."""

    normalized: list[dict] = []
    for candidate in raw:
        raw_bpm = float(candidate["bpm"])
        family = [(raw_bpm, "raw")]
        if raw_bpm < 118:
            family.append((raw_bpm * 2, "double-time"))
        if raw_bpm > 180:
            family.append((raw_bpm / 2, "half-time"))
        for bpm, transform in family:
            master = bpm_master(schema, bpm)
            if not master:
                continue
            item = {
                **candidate,
                "raw_bpm": round(raw_bpm, 4),
                "bpm": round(bpm, 4),
                "transform": transform,
                "master_folder": master["name"],
            }
            prior = next((value for value in normalized if abs(value["bpm"] - bpm) <= 1), None)
            if prior is None:
                normalized.append(item)
            elif float(item.get("weight", 0)) > float(prior.get("weight", 0)):
                normalized.remove(prior)
                normalized.append(item)
    return sorted(normalized, key=lambda value: float(value.get("weight", 0)), reverse=True)


def inspect_audio(path: Path) -> dict[str, Any]:
    """Read immutable file facts without changing tags."""

    result: dict[str, Any] = {
        "path": str(path),
        "file_name": path.name,
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "codec": path.suffix.casefold().lstrip("."),
        "duration_ms": None,
        "bitrate_kbps": None,
        "tags": {},
    }
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:  # Mutagen supports many third-party formats with inconsistent failures.
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


def analyze_audio(path: Path, schema: dict) -> dict[str, Any]:  # pragma: no cover
    """Analyze a new file once; existing Rekordbox files must not call this."""

    try:
        import essentia.standard as essentia
    except ImportError as error:
        raise RuntimeError("Install the 'audio' project extra to analyze new files") from error

    resolved = path.resolve()
    samples = essentia.MonoLoader(filename=str(resolved), sampleRate=44100)()
    rhythm = essentia.RhythmExtractor2013(method="multifeature", minTempo=60, maxTempo=220)
    bpm, ticks, confidence, estimates, _intervals = rhythm(samples)
    key, scale, key_strength = essentia.KeyExtractor(profileType="edma")(samples)
    candidates = [{"bpm": float(bpm), "weight": 1.0, "source": "rhythm-extractor"}]
    candidates.extend(
        {"bpm": float(value), "weight": 0.75, "source": "rhythm-estimate"}
        for value in list(estimates)[:12]
        if float(value) > 0
    )
    normalized = normalize_bpm_candidates(candidates, schema)
    selected = normalized[0] if normalized else None
    candidate_masters = {item["master_folder"] for item in normalized[:3]}
    return {
        "analyzer": "essentia",
        "file_sha256": sha256_file(resolved),
        "raw_bpm": round(float(bpm), 4),
        "extractor_confidence": round(float(confidence), 6),
        "bpm_candidates": normalized,
        "selected_bpm": selected["bpm"] if selected else None,
        "selected_master_folder": selected["master_folder"] if selected else None,
        "bpm_ambiguous": selected is None or len(candidate_masters) > 1,
        "beat_count": len(ticks),
        "key": str(key),
        "scale": str(scale),
        "key_strength": round(float(key_strength), 6),
        "duration_seconds": round(len(samples) / 44100, 3),
    }
