"""Filename normalization and conservative track matching."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from rapidfuzz import fuzz

_VERSION_WORDS = (
    "original|extended|club|radio|vocal|short|long|full|clean|dirty|vip|dub|"
    "deluxe|remaster(?:ed)?|single|anniversary|special|limited|main|album|"
    "instrumental|acapella|bootleg|bonus|mix|edit|version|remix|cut|track"
)
_GENERIC_PARENS = re.compile(
    rf"\s*[\(\[]\s*(?:a\s+cappella|{_VERSION_WORDS})(?:\s+(?:a\s+cappella|{_VERSION_WORDS}))*\s*[\)\]]",
    re.IGNORECASE,
)
_GENERIC_DASH = re.compile(
    rf"\s*[-–]\s*(?:a\s+cappella|{_VERSION_WORDS})(?:\s+(?:a\s+cappella|{_VERSION_WORDS}))*\s*$",
    re.IGNORECASE,
)
_YEAR_PARENS = re.compile(r"\s*[\(\[]\s*\d{4}(?:\s+\w+)?\s*[\)\]]", re.IGNORECASE)
_FORMAT_TAG = re.compile(
    r"\s*[\(\[]\s*(?:flac|mp3|wav|aiff?|opus|320\w*|256\w*|192\w*)\s*[\)\]]",
    re.IGNORECASE,
)
_FEATURED = re.compile(
    r"[\(\[]?(?:feat\.?|ft\.?|featuring|with)\s+[^\)\]]*[\)\]]?", re.IGNORECASE
)
_TRACK_NUMBER = re.compile(r"^[\d]{1,3}[-_\s]+")
_SITE_PREFIX = re.compile(r"^[A-Za-z0-9]+\.[a-zA-Z]{2,4}\s*-\s*")
_STOPWORDS = {"the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "ft", "feat"}


def normalize_text(value: str) -> str:
    value = (
        unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    )
    value = re.sub(r"[^a-z0-9\s]", "", value.casefold())
    return re.sub(r"\s+", " ", value).strip()


def _clean(value: str) -> str:
    for pattern in (
        _GENERIC_PARENS,
        _GENERIC_DASH,
        _YEAR_PARENS,
        _FORMAT_TAG,
        _FEATURED,
    ):
        value = pattern.sub("", value)
    return normalize_text(value)


def parse_filename(stem: str) -> tuple[str, str]:
    value = stem.replace("_", " ")
    had_site_prefix = bool(_SITE_PREFIX.match(value))
    value = _SITE_PREFIX.sub("", value)
    value = _TRACK_NUMBER.sub("", value)
    value = _GENERIC_PARENS.sub("", value)
    value = _YEAR_PARENS.sub("", value)
    value = _FORMAT_TAG.sub("", value).strip(" -")
    if " - " not in value:
        return "", value.strip()
    if had_site_prefix:
        title, artist = value.rsplit(" - ", 1)
        return artist.strip(), title.strip()
    artist, title = value.split(" - ", 1)
    return artist.strip(), title.strip()


def metadata_match_scores(
    candidate_artist: str,
    candidate_title: str,
    reference_artist: str,
    reference_title: str,
) -> tuple[float, float]:
    normal = (
        fuzz.token_set_ratio(_clean(candidate_artist), _clean(reference_artist)) / 100,
        fuzz.token_set_ratio(_clean(candidate_title), _clean(reference_title)) / 100,
    )
    reversed_fields = (
        fuzz.token_set_ratio(_clean(candidate_title), _clean(reference_artist)) / 100,
        fuzz.token_set_ratio(_clean(candidate_artist), _clean(reference_title)) / 100,
    )
    return normal if sum(normal) >= sum(reversed_fields) else reversed_fields


def artist_present_in_filename(reference_artist: str, stem: str) -> bool:
    artist = _clean(reference_artist)
    return bool(artist) and artist in normalize_text(stem.replace("_", " "))


def filename_matches(filename: str, artist: str, title: str) -> bool:
    if not artist or not title:
        return False
    stem = Path(filename.replace("\\", "/")).stem
    candidate_artist, candidate_title = parse_filename(stem)
    artist_score, title_score = metadata_match_scores(
        candidate_artist, candidate_title, artist, title
    )
    return (artist_score >= 0.80 and title_score >= 0.85) or (
        title_score >= 0.95 and artist_present_in_filename(artist, stem)
    )


def version_tokens(value: str) -> set[str]:
    normalized = f" {normalize_text(value)} "
    words = {
        "remix",
        "edit",
        "extended",
        "radio",
        "instrumental",
        "live",
        "sped up",
        "slowed",
    }
    return {word for word in words if f" {normalize_text(word)} " in normalized}
