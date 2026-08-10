"""
classifier.py

Consensus-based electronic music subgenre classifier.

Aggregates metadata from four independent providers:
  - Discogs       (release styles     — weight 3, strongest signal)
  - Last.fm       (community tags     — weight 2)
  - Spotify       (artist genres      — weight 1, contextual)
  - MusicBrainz   (identity resolver  — weight 1, tag voting + canonical metadata)

MusicBrainz runs first in classify() to resolve the canonical artist name,
title, ISRC, and record label. Those resolved values are passed to all other
providers so every search is performed against clean, authoritative metadata.

Each provider exposes fetch_evidence(artist, title, isrc) which returns a
ProviderEvidence dataclass containing the raw genres from that service.
ConsensusClassifier normalises every raw genre string against music_schema_knowledge.json
and aggregates weighted votes to produce a ClassificationResult.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import pylast
import requests

from utils.config import load_env, load_schema
from utils.spotify_auth import get_spotify_client

# ---------------------------------------------------------------------------
# Provider weights
# ---------------------------------------------------------------------------
_WEIGHT_DISCOGS = 3.0       # release-specific, curator-backed
_WEIGHT_LASTFM = 2.0        # release-specific, community
_WEIGHT_SPOTIFY = 0.0       # identity only — artist genres too coarse/sparse to vote

# ---------------------------------------------------------------------------
# Genre alias map  (raw provider string → canonical schema subgenre name)
# Keys are already in _to_key() form: lowercase, NFKC-normalised, single spaces.
# ---------------------------------------------------------------------------
_ALIAS_MAP: dict[str, str] = {
    # Afro House
    "afro house": "Afro House",
    "afro-house": "Afro House",
    "afrohouse": "Afro House",
    # Deep House
    "deep house": "Deep House",
    "deep-house": "Deep House",
    # Melodic House
    "melodic house": "Melodic House",
    "melodic house & techno": "Melodic House",
    "melodic-house": "Melodic House",
    # Progressive House
    "progressive house": "Progressive House",
    "prog house": "Progressive House",
    "progressive-house": "Progressive House",
    # Indie Dance
    "indie dance": "Indie Dance",
    "indie dance/nu disco": "Indie Dance",
    "indie-dance": "Indie Dance",
    # Deep Techno
    "deep techno": "Deep Techno",
    "dark techno": "Deep Techno",
    # Tech House
    "tech house": "Tech House",
    "tech-house": "Tech House",
    "minimal tech house": "Tech House",
    "minimal deep tech": "Tech House",
    # Bass House
    "bass house": "Bass House",
    "bass-house": "Bass House",
    # Electro House
    "electro house": "Electro House",
    "electro-house": "Electro House",
    # Melbourne Bounce
    "melbourne bounce": "Melbourne Bounce",
    "bounce": "Melbourne Bounce",
    # Speed Garage
    "speed garage": "Speed Garage",
    "uk garage": "Speed Garage",
    "garage house": "Speed Garage",
    # Breakbeat
    "breakbeat": "Breakbeat",
    "breaks": "Breakbeat",
    "nu-skool breaks": "Breakbeat",
    "big beat": "Breakbeat",
    "breakbeat/breaks": "Breakbeat",
    # Peak-Time Techno
    "peak time techno": "Peak-Time Techno",
    "peak-time techno": "Peak-Time Techno",
    "peak time / driving": "Peak-Time Techno",
    "techno (peak time / driving)": "Peak-Time Techno",
    "driving techno": "Peak-Time Techno",
    "rave": "Peak-Time Techno",
    # Minimal Techno
    "minimal techno": "Minimal Techno",
    "minimal": "Minimal Techno",
    "minimal/tech house": "Minimal Techno",
    "microhouse": "Minimal Techno",
    # Melodic Techno
    "melodic techno": "Melodic Techno",
    "melodic techno/house": "Melodic Techno",
    "trance/techno": "Melodic Techno",
    # Hard Groove
    "hard groove": "Hard Groove",
    # High Tech
    "high tech": "High Tech",
    "high-tech": "High Tech",
    # Psytrance
    "psytrance": "Psytrance",
    "psy-trance": "Psytrance",
    "psy trance": "Psytrance",
    "progressive psytrance": "Psytrance",
    "full-on psytrance": "Psytrance",
    "goa trance": "Psytrance",
    # Hard House
    "hard house": "Hard House",
    "hard-house": "Hard House",
    # Hard Techno
    "hard techno": "Hard Techno",
    "hard-techno": "Hard Techno",
    # Industrial
    "industrial": "Industrial",
    "industrial techno": "Industrial",
    # Neo Trance
    "neo trance": "Neo Trance",
    "neo-trance": "Neo Trance",
    # Hypertechno
    "hypertechno": "Hypertechno",
    "hyper techno": "Hypertechno",
    "hyper-techno": "Hypertechno",
    # Hard Dance
    "hard dance": "Hard Dance",
    "hard-dance": "Hard Dance",
    # Hard Trance
    "hard trance": "Hard Trance",
    "hard-trance": "Hard Trance",
    # Hardtekk
    "hardtekk": "Hardtekk",
    "hard tekk": "Hardtekk",
    # Hardstyle
    "hardstyle": "Hardstyle",
    "hard style": "Hardstyle",
    # Schranz
    "schranz": "Schranz",
    # Drum & Bass
    "drum & bass": "Drum & Bass",
    "drum and bass": "Drum & Bass",
    "dnb": "Drum & Bass",
    "d&b": "Drum & Bass",
    "liquid drum & bass": "Drum & Bass",
    "liquid dnb": "Drum & Bass",
    "jump-up": "Drum & Bass",
    "neurofunk": "Drum & Bass",
    "drumfunk": "Drum & Bass",
}

# ---------------------------------------------------------------------------
# Broad genre fallback map  (raw provider string → broad genre bucket)
# Only fires when no schema subgenre wins the specific vote.
# Keys are in _to_key() form. Intentionally sparse — only truly generic strings.
# Specific-sounding strings ("progressive trance", "deep house") are NOT here;
# those belong in _ALIAS_MAP or warrant a new schema entry.
# ---------------------------------------------------------------------------
_BROAD_GENRE_MAP: dict[str, str] = {
    # House (broad)
    "house": "House",
    "jackin house": "House",
    "funky house": "House",
    "vocal house": "House",
    "nu disco": "House",
    "funky/groove/jackin": "House",
    "soulful house": "House",
    "future house": "House",
    # Techno (broad)
    "techno": "Techno",
    "german techno": "Techno",
    # Trance (broad) — specific variants (psytrance, goa trance, hard trance) are in _ALIAS_MAP
    "trance": "Trance",
    "vocal trance": "Trance",
    "uplifting trance": "Trance",
    "epic trance": "Trance",
    "anthem trance": "Trance",
    # Drum & Bass (strings NOT already in _ALIAS_MAP)
    "jungle": "Drum & Bass",
    "liquid funk": "Drum & Bass",
    "darkstep": "Drum & Bass",
    "footwork": "Drum & Bass",
    # Hard Dance (strings NOT already in _ALIAS_MAP as specific subgenres)
    "hardcore": "Hard Dance",
    "gabber": "Hard Dance",
    "happy hardcore": "Hard Dance",
    "rawstyle": "Hard Dance",
    "early hardcore": "Hard Dance",
}


def _to_key(s: str) -> str:
    """NFKC-normalise, lowercase, collapse whitespace — used for alias-map lookups."""
    s = unicodedata.normalize("NFKC", s)
    return " ".join(s.lower().split())


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ProviderEvidence:
    """Raw evidence from a single metadata provider."""
    provider: str
    genres: list[str]   # raw genre/style strings returned by the provider
    weight: float       # voting weight assigned to this provider
    raw: dict           # full raw payload for debugging / explainability


@dataclass
class ClassificationResult:
    """Result of the consensus classification."""
    subgenre: str | None        # winning schema subgenre, or None if undetermined
    confidence: float           # winning_votes / total_votes (0–1)
    evidence: list[ProviderEvidence]
    record_label: str | None
    canonical_artist: str | None = None   # resolved by MusicBrainz; use for downloads
    canonical_title: str | None = None    # resolved by MusicBrainz; use for downloads
    is_broad_fallback: bool = False        # True when only a broad genre could be determined

    def explain(self) -> str:
        """Return a human-readable explanation of the classification."""
        lines: list[str] = []
        if self.subgenre and self.is_broad_fallback:
            lines.append(
                f'Classification: "{self.subgenre}" (confidence: {self.confidence:.0%})'
                " — broad genre fallback (no specific subgenre identified)"
            )
        elif self.subgenre:
            lines.append(f'Classification: "{self.subgenre}" (confidence: {self.confidence:.0%})')
        else:
            lines.append("Classification: undetermined (no consensus reached)")
        if self.record_label:
            lines.append(f"Label: {self.record_label}")
        if self.canonical_artist or self.canonical_title:
            lines.append(
                f"Resolved: {self.canonical_artist or '?'} — {self.canonical_title or '?'}"
            )
        lines.append("")
        lines.append("Evidence:")
        for ev in self.evidence:
            if ev.genres:
                lines.append(f"  {ev.provider}: {', '.join(ev.genres)}")
            else:
                lines.append(f"  {ev.provider}: no relevant genres found")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Genre normaliser
# ---------------------------------------------------------------------------

class GenreNormalizer:
    """Maps raw provider genre strings to canonical schema subgenre names."""

    def __init__(self, schema: dict) -> None:
        # The shared iterator keeps the legacy classifier readable while the
        # live schema is migrated from flat v1 folders to nested v2 placements.
        from utils.schema import iter_subgenres

        self._schema_names = {
            context["subgenre"]["name"] for context in iter_subgenres(schema)
        }

    def normalize(self, raw: str) -> str | None:
        """Return the canonical schema subgenre name for *raw*, or None if unknown."""
        if not raw or not raw.strip():
            return None
        key = _to_key(raw)
        # 1. Alias map lookup
        mapped = _ALIAS_MAP.get(key)
        if mapped and mapped in self._schema_names:
            return mapped
        # 2. Direct schema name match (case-insensitive)
        for name in self._schema_names:
            if _to_key(name) == key:
                return name
        return None


# ---------------------------------------------------------------------------
# Metadata providers
# ---------------------------------------------------------------------------

class SpotifyProvider:
    """Fetches artist-level genres from Spotify. Weight: 1 (contextual)."""

    def __init__(self, sp) -> None:
        self._sp = sp

    def fetch_evidence(
        self, artist: str, title: str, isrc: str | None = None
    ) -> ProviderEvidence:
        try:
            # Build query — prefer ISRC for precision, fall back to strict then loose
            if isrc:
                queries = [f"isrc:{isrc}"]
            else:
                queries = [f"artist:{artist} track:{title}", f"{artist} {title}"]

            items: list = []
            for query in queries:
                results = self._sp.search(q=query, type="track", limit=1)
                items = results.get("tracks", {}).get("items", [])
                if items:
                    break

            if not items:
                return ProviderEvidence(
                    provider="Spotify", genres=[], weight=_WEIGHT_SPOTIFY, raw={}
                )

            track_item = items[0]
            artist_id = track_item["artists"][0]["id"]

            # 1. Try artist endpoint
            artist_data = self._sp.artist(artist_id)
            raw_genres: list[str] = artist_data.get("genres", [])
            genre_source = "artist_id"

            # 2. Fallback: direct artist search (weight=0; kept for identity/debugging)
            if not raw_genres:
                artist_search = self._sp.search(q=artist, type="artist", limit=1)
                artist_items = artist_search.get("artists", {}).get("items", [])
                if artist_items:
                    raw_genres = artist_items[0].get("genres", [])
                    genre_source = "artist_search"

            return ProviderEvidence(
                provider="Spotify",
                genres=raw_genres,
                weight=_WEIGHT_SPOTIFY,
                raw={"artist_id": artist_id, "artist_genres": raw_genres, "genre_source": genre_source},
            )
        except Exception as exc:
            return ProviderEvidence(
                provider="Spotify",
                genres=[],
                weight=_WEIGHT_SPOTIFY,
                raw={"error": str(exc)},
            )


class DiscogsProvider:
    """Fetches release styles from Discogs. Weight: 3 (strongest — release-specific, curated)."""

    _SEARCH_URL = "https://api.discogs.com/database/search"
    _HEADERS = {"User-Agent": "DJMusicClassifier/1.0 +https://github.com/user/dj"}

    def __init__(self, token: str) -> None:
        self._token = token

    def fetch_evidence(
        self, artist: str, title: str, isrc: str | None = None
    ) -> ProviderEvidence:
        try:
            headers = {
                **self._HEADERS,
                "Authorization": f"Discogs token={self._token}",
            }
            # Try strict artist+track search first; fall back to free-text query
            search_params_list = [
                {"type": "release", "artist": artist, "track": title, "per_page": 5},
                {"type": "release", "q": f"{artist} {title}", "per_page": 5},
            ]
            results: list = []
            last_data: dict = {}
            for params in search_params_list:
                resp = requests.get(
                    self._SEARCH_URL, params=params, headers=headers, timeout=10
                )
                resp.raise_for_status()
                last_data = resp.json()
                results = last_data.get("results", [])
                if results:
                    break

            if not results:
                return ProviderEvidence(
                    provider="Discogs", genres=[], weight=_WEIGHT_DISCOGS, raw=last_data
                )
            # Prefer styles (release-specific); fall back to broad genre if no styles
            genres: list[str] = results[0].get("style", [])
            genre_source = "style"
            if not genres:
                genres = results[0].get("genre", [])
                genre_source = "genre_fallback"
            return ProviderEvidence(
                provider="Discogs",
                genres=genres,
                weight=_WEIGHT_DISCOGS,
                raw={"results_count": len(results), "styles": genres, "genre_source": genre_source},
            )
        except Exception as exc:
            return ProviderEvidence(
                provider="Discogs",
                genres=[],
                weight=_WEIGHT_DISCOGS,
                raw={"error": str(exc)},
            )


# MusicBrainzProvider removed — identity resolution added noise without value.
# Spotify and MusicBrainz were weight-0 (identity only); removing MB simplifies
# the pipeline to the two providers that actually vote: Discogs and Last.fm.


class LastFmProvider:
    """Fetches community track tags from Last.fm. Weight: 2."""

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._network = pylast.LastFMNetwork(api_key=api_key, api_secret=api_secret)

    def fetch_evidence(
        self, artist: str, title: str, isrc: str | None = None
    ) -> ProviderEvidence:
        try:
            track = self._network.get_track(artist, title)
            top_tags = track.get_top_tags(limit=10)
            tag_names: list[str] = [t.item.get_name() for t in top_tags]

            # Fallback: if track has no tags, use artist-level tags
            source = "track"
            if not tag_names:
                artist_obj = self._network.get_artist(artist)
                artist_tags = artist_obj.get_top_tags(limit=10)
                tag_names = [t.item.get_name() for t in artist_tags]
                source = "artist"

            return ProviderEvidence(
                provider="Last.fm",
                genres=tag_names,
                weight=_WEIGHT_LASTFM,
                raw={"tags": tag_names, "source": source},
            )
        except Exception as exc:
            return ProviderEvidence(
                provider="Last.fm",
                genres=[],
                weight=_WEIGHT_LASTFM,
                raw={"error": str(exc)},
            )


# ---------------------------------------------------------------------------
# Consensus classifier
# ---------------------------------------------------------------------------

class ConsensusClassifier:
    """
    Aggregates evidence from all providers and determines the most likely
    schema subgenre through weighted consensus voting.

    Provider instances can be injected for testing.
    """

    def __init__(
        self,
        env: dict | None = None,
        schema: dict | None = None,
        *,
        spotify_provider: SpotifyProvider | None = None,
        discogs_provider: DiscogsProvider | None = None,
        lastfm_provider: LastFmProvider | None = None,
    ) -> None:
        if env is None:
            env = load_env()
        if schema is None:
            schema = load_schema()

        self._normalizer = GenreNormalizer(schema)

        self._spotify = spotify_provider or SpotifyProvider(get_spotify_client())
        self._discogs = discogs_provider or DiscogsProvider(env["DISCOGS_TOKEN"])
        self._lastfm = lastfm_provider or LastFmProvider(
            env["LASTFM_API_KEY"], env["LASTFM_SHARED_SECRET"]
        )

    def classify(
        self, artist: str, title: str, isrc: str | None = None
    ) -> ClassificationResult:
        """Classify a track by aggregating metadata from all providers."""
        discogs_ev = self._discogs.fetch_evidence(artist, title, isrc)
        lastfm_ev  = self._lastfm.fetch_evidence(artist, title, isrc)
        spotify_ev = self._spotify.fetch_evidence(artist, title, isrc)

        evidence = [discogs_ev, lastfm_ev, spotify_ev]

        # Weighted vote accumulation
        votes: dict[str, float] = {}
        for ev in evidence:
            if ev.weight == 0:
                continue
            for raw_genre in ev.genres:
                normalised = self._normalizer.normalize(raw_genre)
                if normalised:
                    votes[normalised] = votes.get(normalised, 0.0) + ev.weight

        if not votes:
            # Broad genre fallback — only when no specific subgenre won
            broad_votes: dict[str, float] = {}
            for ev in evidence:
                if ev.weight == 0:
                    continue
                for raw_genre in ev.genres:
                    broad = _BROAD_GENRE_MAP.get(_to_key(raw_genre))
                    if broad:
                        broad_votes[broad] = broad_votes.get(broad, 0.0) + ev.weight

            if broad_votes:
                winner = max(broad_votes, key=lambda k: broad_votes[k])
                total = sum(broad_votes.values())
                return ClassificationResult(
                    subgenre=winner,
                    confidence=broad_votes[winner] / total if total > 0 else 0.0,
                    evidence=evidence,
                    record_label=None,
                    is_broad_fallback=True,
                )

            return ClassificationResult(
                subgenre=None,
                confidence=0.0,
                evidence=evidence,
                record_label=None,
            )

        winner = max(votes, key=lambda k: votes[k])
        total = sum(votes.values())
        confidence = votes[winner] / total if total > 0 else 0.0

        return ClassificationResult(
            subgenre=winner,
            confidence=confidence,
            evidence=evidence,
            record_label=None,
        )
