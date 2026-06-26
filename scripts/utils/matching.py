"""utils/matching.py

Shared preprocessing and fuzzy-matching utilities.
Single source of truth used by 03_delta_scan, 03b_reconcile_unsorted, and 04_acquire.

Design goals
------------
* Strip *generic* version labels that add no matching signal:
  (Original Mix), (Extended Mix), (Radio Edit), - Original Mix, etc.
* Preserve *named* remix/edit suffixes that DO carry signal:
  (Jamie Jones Remix), (Victor Ruiz Remix), - RAUMM Remix, etc.
* Normalise Unicode, whitespace, punctuation consistently.
* Three-stage filename quality gate (Stage 0 regex → Stage 1 fuzzy → Stage 2 fallback).
"""

import re
import unicodedata
from pathlib import Path

from rapidfuzz import fuzz as rf

# ---------------------------------------------------------------------------
# Generic version-label vocabulary
# ---------------------------------------------------------------------------
# These words, alone or in combination, identify a generic version suffix that
# adds no matching value (e.g. "Original Mix", "Extended Deluxe Mix").
# Any bracketed/dash content composed *exclusively* of words from this list is
# stripped; anything with extra words (like a remixer's name) is kept.

_V = (
    "original|extended|club|radio|vocal|short|long|full|clean|dirty|vip|dub|"
    "deluxe|remaster(?:ed)?|single|anniversary|special|limited|main|album|"
    "instrumental|acapella|bootleg|bonus|mix|edit|version|remix|cut|track"
)

# Parenthetical/bracket form  – strips (Original Mix), [Extended Deluxe Mix], etc.
# Does NOT strip (Jamie Jones Remix) because "Jamie" and "Jones" are not in _V.
RE_GENERIC_PARENS = re.compile(
    rf"\s*[\(\[]\s*(?:a\s+cappella|{_V})(?:\s+(?:a\s+cappella|{_V}))*\s*[\)\]]",
    re.IGNORECASE,
)

# Dash/en-dash suffix form  – strips "- Original Mix", "– Radio Edit", etc.
# Does NOT strip "- Victor Ruiz Remix" because "Victor" and "Ruiz" are not in _V.
RE_GENERIC_DASH = re.compile(
    rf"\s*[-–]\s*(?:a\s+cappella|{_V})(?:\s+(?:a\s+cappella|{_V}))*\s*$",
    re.IGNORECASE,
)

# Year-only or year+single-word parentheticals: (2024), [2024], (2024 Remaster)
RE_YEAR_PARENS = re.compile(
    r"\s*[\(\[]\s*\d{4}(?:\s+\w+)?\s*[\)\]]",
    re.IGNORECASE,
)

# Audio format/quality tags sometimes embedded in filenames: [FLAC], [320kbps]
RE_FORMAT_TAG = re.compile(
    r"\s*[\(\[]\s*(?:flac|mp3|wav|aiff?|opus|320\w*|256\w*|192\w*)\s*[\)\]]",
    re.IGNORECASE,
)

# Featured artist / collaborator tags
RE_FEAT = re.compile(
    r"[\(\[]?(?:feat\.?|ft\.?|featuring|with)\s+[^\)\]]*[\)\]]?",
    re.IGNORECASE,
)

# Leading track-number prefixes: "01-", "02 ", "003_"
RE_TRACK_NUM = re.compile(r"^[\d]{1,3}[-_\s]+")

# Download-tool site prefixes: "SpotiDownloader.com - ", "Beatport.com - "
RE_SITE_PREFIX = re.compile(r"^[A-Za-z0-9]+\.[a-zA-Z]{2,4}\s*-\s*")

# ---------------------------------------------------------------------------
# Stopwords (excluded from Stage-0 regex word list)
# ---------------------------------------------------------------------------
STOPWORDS = {"the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "ft", "feat"}

# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def normalize(s: str) -> str:
    """Unicode-normalize to ASCII, lowercase, strip punctuation, collapse whitespace.

    Uses NFKD decomposition so accented/typographic chars are handled:
      Tiësto → tiesto, em-dash → stripped, curly quotes → stripped.
    """
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def clean(s: str) -> str:
    """Deep-clean a string for fuzzy matching.

    Strips generic version suffixes (both parenthetical and dash forms), year
    parentheticals, format tags, and featured-artist tags, then normalizes.

    Named remixes – e.g. '(Jamie Jones Remix)', '- Victor Ruiz Remix' – are
    intentionally preserved because they carry matching signal.
    """
    s = RE_GENERIC_PARENS.sub("", s)
    s = RE_GENERIC_DASH.sub("", s)
    s = RE_YEAR_PARENS.sub("", s)
    s = RE_FORMAT_TAG.sub("", s)
    s = RE_FEAT.sub("", s)
    return normalize(s).strip()


def preprocess_stem(stem: str) -> tuple[str, str]:
    """Parse a filename stem into (candidate_artist, candidate_title).

    Handles:
    - Underscores as spaces
    - Leading track-number prefixes: "01-", "02 ", "003_"
    - Download-tool prefixes: "SpotiDownloader.com - ", "Beatport.com - "
    - Generic version suffixes in brackets: "(Original Mix)", "[Extended Edit]"
    - Year / format parentheticals
    - SpotiDownloader format: "Title - Remix Info - Artist" (artist is LAST segment)

    Returns ("", cleaned_stem) when no " - " separator is found.
    """
    s = stem.replace("_", " ")
    had_site_prefix = bool(RE_SITE_PREFIX.match(s))
    s = RE_SITE_PREFIX.sub("", s)
    s = RE_TRACK_NUM.sub("", s)
    s = RE_GENERIC_PARENS.sub("", s)
    s = RE_YEAR_PARENS.sub("", s)
    s = RE_FORMAT_TAG.sub("", s)
    s = s.strip(" -")

    if " - " not in s:
        return "", s.strip()

    if had_site_prefix:
        # SpotiDownloader writes "Title - Remix Info - Artist"; artist is last.
        title, artist = s.rsplit(" - ", 1)
        return artist.strip(), title.strip()

    parts = s.split(" - ", 1)
    return parts[0].strip(), parts[1].strip()


def sig_words(s: str) -> list[str]:
    """Return significant (non-stopword, length > 2) normalized words."""
    return [w for w in normalize(s).split() if len(w) > 2 and w not in STOPWORDS]


def match_scores(
    cand_artist: str,
    cand_title: str,
    ref_artist: str,
    ref_title: str,
) -> tuple[float, float]:
    """Return best (artist_score, title_score) trying both field orderings.

    Applies clean() preprocessing, then uses rapidfuzz token_set_ratio.
    Tries both normal order and swapped order (title-as-artist, artist-as-title)
    to handle filenames where the fields are reversed.
    """
    ca, ra = clean(cand_artist), clean(ref_artist)
    ct, rt = clean(cand_title), clean(ref_title)

    a1 = rf.token_set_ratio(ca, ra) / 100
    t1 = rf.token_set_ratio(ct, rt) / 100
    a2 = rf.token_set_ratio(ct, ra) / 100
    t2 = rf.token_set_ratio(ca, rt) / 100

    if a1 + t1 >= a2 + t2:
        return a1, t1
    return a2, t2


def artist_found_in_stem(ref_artist: str, stem: str) -> bool:
    """Return True if ref_artist appears as a normalized substring in the stem."""
    ra = normalize(clean(ref_artist))
    s = normalize(stem.replace("_", " "))
    return bool(ra) and ra in s


def filename_matches(filename: str, artist: str, title: str) -> bool:
    """Three-stage quality check: is this filename a plausible match for artist/title?

    Stage 0 (regex):  all significant title words + ≥1 artist word appear as
                      \\b word-boundary matches in the normalized stem.
    Stage 1 (fuzzy):  artist token_set_ratio ≥ 0.80 AND title ≥ 0.85
    Stage 2 (fallback): title ≥ 0.95 AND artist found as substring in stem

    Returns True immediately on any passing stage. The gate is skipped when
    either artist or title is an empty string.
    """
    raw_stem = filename.replace("\\", "/").split("/")[-1]
    raw_stem = Path(raw_stem).stem
    norm_stem = normalize(raw_stem.replace("_", " "))

    # Stage 0 – fast regex word-boundary check
    tw = sig_words(title)
    aw = sig_words(artist)
    if tw and all(re.search(r"\b" + re.escape(w) + r"\b", norm_stem) for w in tw):
        if not aw or any(re.search(r"\b" + re.escape(w) + r"\b", norm_stem) for w in aw):
            return True

    # Stages 1 & 2 – fuzzy token-set-ratio
    cand_artist, cand_title = preprocess_stem(raw_stem)
    ca, ra = clean(cand_artist), clean(artist)
    ct, rt = clean(cand_title), clean(title)
    a1 = rf.token_set_ratio(ca, ra) / 100
    t1 = rf.token_set_ratio(ct, rt) / 100
    a2 = rf.token_set_ratio(ct, ra) / 100
    t2 = rf.token_set_ratio(ca, rt) / 100
    a_score, t_score = (a1, t1) if a1 + t1 >= a2 + t2 else (a2, t2)

    stage1 = a_score >= 0.80 and t_score >= 0.85
    artist_in_stem = bool(ra) and ra in norm_stem
    stage2 = (not stage1) and t_score >= 0.95 and artist_in_stem
    return stage1 or stage2
