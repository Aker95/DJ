"""Read audio facts and analyze only newly acquired files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile

from .io import sha256_file

AUDIO_EXTENSIONS = {
    ".aac", ".aif", ".aiff", ".alac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"
}


def inspect_audio(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
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
    except Exception:
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


def analyze_new_file(path: Path) -> dict[str, Any]:  # pragma: no cover - real audio integration
    """Estimate BPM and key for an intake file with Essentia."""

    try:
        import essentia.standard as essentia
    except ImportError as error:
        raise RuntimeError("Essentia is required to analyze newly acquired files") from error
    samples = essentia.MonoLoader(filename=str(path.resolve()), sampleRate=44100)()
    rhythm = essentia.RhythmExtractor2013(method="multifeature", minTempo=60, maxTempo=220)
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
