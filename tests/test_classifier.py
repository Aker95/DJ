"""
tests/test_classifier.py

Unit tests for the consensus-based subgenre classifier.
All external API calls are mocked — no network required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from utils.classifier import (
    ClassificationResult,
    ConsensusClassifier,
    DiscogsProvider,
    GenreNormalizer,
    LastFmProvider,
    ProviderEvidence,
    SpotifyProvider,
    _BROAD_GENRE_MAP,
    _WEIGHT_DISCOGS,
    _WEIGHT_LASTFM,
    _WEIGHT_SPOTIFY,
)


# ---------------------------------------------------------------------------
# Shared minimal schema fixture (extends conftest.py sample_schema with the
# subgenres needed for classification tests)
# ---------------------------------------------------------------------------

@pytest.fixture
def full_schema():
    """Schema covering every subgenre referenced in the alias map tests."""
    subgenres = [
        "Afro House", "Deep House", "Melodic House", "Progressive House",
        "Indie Dance", "Deep Techno",
        "Tech House", "Bass House", "Electro House", "Melbourne Bounce",
        "Speed Garage", "Breakbeat",
        "Peak-Time Techno", "Minimal Techno", "Melodic Techno", "Hard Groove",
        "High Tech", "Psytrance",
        "Hard House", "Hard Techno", "Industrial", "Neo Trance", "Hypertechno",
        "Hard Dance", "Hard Trance", "Hardtekk", "Hardstyle", "Schranz",
        "Drum & Bass",
    ]
    return {
        "master_folders": [
            {
                "id": "f1",
                "name": "All Genres",
                "bpm_min": 120,
                "bpm_max": 175,
                "subgenres": [
                    {"name": n, "local_folder": f"music/{n.lower()}", "spotify_playlist": None, "notes": None}
                    for n in subgenres
                ],
            }
        ]
    }


def _make_evidence(provider: str, genres: list[str], weight: float) -> ProviderEvidence:
    return ProviderEvidence(provider=provider, genres=genres, weight=weight, raw={})


# ===========================================================================
# TestGenreNormalizer
# ===========================================================================

class TestGenreNormalizer:
    """Alias map and direct schema name matching."""

    def test_canonical_name_returned_unchanged(self, full_schema):
        n = GenreNormalizer(full_schema)
        assert n.normalize("Tech House") == "Tech House"

    def test_hyphenated_alias(self, full_schema):
        n = GenreNormalizer(full_schema)
        assert n.normalize("tech-house") == "Tech House"

    def test_case_insensitive_alias(self, full_schema):
        n = GenreNormalizer(full_schema)
        assert n.normalize("TECH HOUSE") == "Tech House"

    def test_minimal_maps_to_minimal_techno(self, full_schema):
        n = GenreNormalizer(full_schema)
        assert n.normalize("minimal") == "Minimal Techno"

    def test_minimal_techno_direct(self, full_schema):
        n = GenreNormalizer(full_schema)
        assert n.normalize("minimal techno") == "Minimal Techno"

    def test_drum_and_bass_variants(self, full_schema):
        n = GenreNormalizer(full_schema)
        assert n.normalize("drum and bass") == "Drum & Bass"
        assert n.normalize("dnb") == "Drum & Bass"
        assert n.normalize("d&b") == "Drum & Bass"
        assert n.normalize("neurofunk") == "Drum & Bass"

    def test_peak_time_techno_with_hyphen(self, full_schema):
        n = GenreNormalizer(full_schema)
        assert n.normalize("peak-time techno") == "Peak-Time Techno"
        assert n.normalize("peak time techno") == "Peak-Time Techno"
        assert n.normalize("driving techno") == "Peak-Time Techno"

    def test_psytrance_variants(self, full_schema):
        n = GenreNormalizer(full_schema)
        assert n.normalize("psy-trance") == "Psytrance"
        assert n.normalize("psy trance") == "Psytrance"
        assert n.normalize("goa trance") == "Psytrance"

    def test_hypertechno_variants(self, full_schema):
        n = GenreNormalizer(full_schema)
        assert n.normalize("hyper techno") == "Hypertechno"
        assert n.normalize("hyper-techno") == "Hypertechno"

    def test_speed_garage_aliases(self, full_schema):
        n = GenreNormalizer(full_schema)
        assert n.normalize("uk garage") == "Speed Garage"
        assert n.normalize("garage house") == "Speed Garage"

    def test_extra_whitespace_collapsed(self, full_schema):
        n = GenreNormalizer(full_schema)
        assert n.normalize("  Tech  House  ") == "Tech House"

    def test_unknown_genre_returns_none(self, full_schema):
        n = GenreNormalizer(full_schema)
        assert n.normalize("Polka") is None

    def test_empty_string_returns_none(self, full_schema):
        n = GenreNormalizer(full_schema)
        assert n.normalize("") is None

    def test_whitespace_only_returns_none(self, full_schema):
        n = GenreNormalizer(full_schema)
        assert n.normalize("   ") is None

    def test_broad_genre_house_returns_none(self, full_schema):
        # "house" alone is too broad — not in alias map or schema names
        n = GenreNormalizer(full_schema)
        assert n.normalize("house") is None

    def test_industrial_techno_maps_to_industrial(self, full_schema):
        n = GenreNormalizer(full_schema)
        assert n.normalize("industrial techno") == "Industrial"


# ===========================================================================
# TestSpotifyProvider
# ===========================================================================

class TestSpotifyProvider:
    """Artist-genre evidence from Spotify."""

    def _make_sp(self, track_items, artist_genres):
        sp = MagicMock()
        sp.search.return_value = {"tracks": {"items": track_items}}
        sp.artist.return_value = {"genres": artist_genres, "id": "artist_id_123"}
        # album returns empty genres by default
        sp.album.return_value = {"genres": []}
        return sp

    def test_returns_artist_genres(self):
        sp = self._make_sp(
            track_items=[{"artists": [{"id": "a1"}], "album": {"id": "alb1"}}],
            artist_genres=["tech house", "house"],
        )
        provider = SpotifyProvider(sp)
        ev = provider.fetch_evidence("Someone", "Something")

        assert ev.provider == "Spotify"
        assert ev.weight == _WEIGHT_SPOTIFY
        assert "tech house" in ev.genres
        assert "house" in ev.genres
        assert ev.raw["genre_source"] == "artist_id"

    def test_empty_genres_when_no_tracks(self):
        sp = self._make_sp(track_items=[], artist_genres=[])
        provider = SpotifyProvider(sp)
        ev = provider.fetch_evidence("Unknown", "Track")

        assert ev.genres == []
        assert ev.weight == _WEIGHT_SPOTIFY

    def test_uses_isrc_query_when_provided(self):
        sp = self._make_sp(
            track_items=[{"artists": [{"id": "a1"}], "album": {"id": "alb1"}}],
            artist_genres=["minimal techno"],
        )
        provider = SpotifyProvider(sp)
        provider.fetch_evidence("Artist", "Title", isrc="USUM71900001")

        call_kwargs = sp.search.call_args
        assert "isrc:USUM71900001" in call_kwargs[1].get("q", "") or \
               "isrc:USUM71900001" in call_kwargs[0][0]

    def test_falls_back_to_direct_artist_search_when_artist_id_empty(self):
        """When sp.artist() returns [], sp.search(type='artist') provides genres."""
        sp = MagicMock()
        track_response = {"tracks": {"items": [{"artists": [{"id": "a1"}], "album": {"id": "alb1"}}]}}
        artist_search_response = {"artists": {"items": [{"id": "a1", "genres": ["deep house"]}]}}
        sp.search.side_effect = [track_response, artist_search_response]
        sp.artist.return_value = {"genres": []}
        sp.album.return_value = {"genres": []}

        provider = SpotifyProvider(sp)
        ev = provider.fetch_evidence("Someone", "Something")

        assert "deep house" in ev.genres
        assert ev.raw["genre_source"] == "artist_search"

    def test_returns_empty_evidence_on_exception(self):
        sp = MagicMock()
        sp.search.side_effect = Exception("network error")
        provider = SpotifyProvider(sp)
        ev = provider.fetch_evidence("Artist", "Title")

        assert ev.genres == []
        assert "error" in ev.raw


# ===========================================================================
# TestDiscogsProvider
# ===========================================================================

class TestDiscogsProvider:
    """Release-style evidence from Discogs."""

    def _make_response(self, results):
        resp = MagicMock()
        resp.json.return_value = {"results": results}
        resp.raise_for_status.return_value = None
        return resp

    def test_returns_styles_from_first_result(self):
        with patch("utils.classifier.requests.get") as mock_get:
            mock_get.return_value = self._make_response(
                [{"style": ["Tech House", "Minimal"], "genre": ["Electronic"]}]
            )
            provider = DiscogsProvider(token="fake_token")
            ev = provider.fetch_evidence("Victor Ruiz", "Inception")

        assert ev.provider == "Discogs"
        assert ev.weight == _WEIGHT_DISCOGS
        assert "Tech House" in ev.genres
        assert "Minimal" in ev.genres

    def test_empty_genres_when_no_results(self):
        with patch("utils.classifier.requests.get") as mock_get:
            mock_get.return_value = self._make_response([])
            provider = DiscogsProvider(token="fake_token")
            ev = provider.fetch_evidence("Nobody", "Nothing")

        assert ev.genres == []

    def test_falls_back_to_genre_when_no_styles(self):
        """When no style key, broad genre field is used as fallback."""
        with patch("utils.classifier.requests.get") as mock_get:
            mock_get.return_value = self._make_response(
                [{"genre": ["Deep House"]}]  # no "style" key
            )
            provider = DiscogsProvider(token="fake_token")
            ev = provider.fetch_evidence("Artist", "Track")

        assert "Deep House" in ev.genres
        assert ev.raw["genre_source"] == "genre_fallback"

    def test_returns_empty_evidence_on_exception(self):
        with patch("utils.classifier.requests.get") as mock_get:
            mock_get.side_effect = Exception("timeout")
            provider = DiscogsProvider(token="fake_token")
            ev = provider.fetch_evidence("Artist", "Track")

        assert ev.genres == []
        assert "error" in ev.raw


# ===========================================================================
# TestMusicBrainzProvider
# ===========================================================================

# ===========================================================================
# TestLastFmProvider
# ===========================================================================

# ===========================================================================
# TestLastFmProvider
# ===========================================================================

class TestLastFmProvider:
    """Community tag evidence from Last.fm."""

    def _make_tag(self, name: str):
        tag = MagicMock()
        tag.item.get_name.return_value = name
        return tag

    def test_returns_top_tags(self):
        with patch("utils.classifier.pylast.LastFMNetwork") as MockNetwork:
            mock_track = MagicMock()
            mock_track.get_top_tags.return_value = [
                self._make_tag("Tech House"),
                self._make_tag("Minimal"),
            ]
            MockNetwork.return_value.get_track.return_value = mock_track

            provider = LastFmProvider(api_key="key", api_secret="secret")
            ev = provider.fetch_evidence("Pig & Dan", "Helix")

        assert ev.provider == "Last.fm"
        assert ev.weight == _WEIGHT_LASTFM
        assert "Tech House" in ev.genres
        assert "Minimal" in ev.genres

    def test_empty_genres_when_no_tags(self):
        with patch("utils.classifier.pylast.LastFMNetwork") as MockNetwork:
            mock_track = MagicMock()
            mock_track.get_top_tags.return_value = []
            MockNetwork.return_value.get_track.return_value = mock_track

            provider = LastFmProvider(api_key="key", api_secret="secret")
            ev = provider.fetch_evidence("Unknown", "Track")

        assert ev.genres == []

    def test_returns_empty_evidence_on_exception(self):
        with patch("utils.classifier.pylast.LastFMNetwork") as MockNetwork:
            MockNetwork.return_value.get_track.side_effect = Exception("api error")

            provider = LastFmProvider(api_key="key", api_secret="secret")
            ev = provider.fetch_evidence("Artist", "Track")

        assert ev.genres == []
        assert "error" in ev.raw


# ===========================================================================
# TestConsensusClassifier
# ===========================================================================

class TestConsensusClassifier:
    """Weighted consensus voting and ClassificationResult output."""

    def _make_classifier(self, schema, discogs_genres, lastfm_genres, spotify_genres):
        """Create a ConsensusClassifier with injected mock providers."""
        discogs = MagicMock(spec=DiscogsProvider)
        discogs.fetch_evidence.return_value = _make_evidence(
            "Discogs", discogs_genres, _WEIGHT_DISCOGS
        )
        lastfm = MagicMock(spec=LastFmProvider)
        lastfm.fetch_evidence.return_value = _make_evidence(
            "Last.fm", lastfm_genres, _WEIGHT_LASTFM
        )
        spotify = MagicMock(spec=SpotifyProvider)
        spotify.fetch_evidence.return_value = _make_evidence(
            "Spotify", spotify_genres, _WEIGHT_SPOTIFY
        )

        return ConsensusClassifier(
            env={},
            schema=schema,
            spotify_provider=spotify,
            discogs_provider=discogs,
            lastfm_provider=lastfm,
        )

    def test_winner_is_most_voted_subgenre(self, full_schema):
        # Discogs: Tech House(3) + Minimal Techno(3)
        # Last.fm: Tech House(2)
        # Spotify: Tech House(1)
        # Tech House total = 6, Minimal Techno = 3
        clf = self._make_classifier(
            full_schema,
            discogs_genres=["Tech House", "Minimal"],
            lastfm_genres=["Tech House"],
            spotify_genres=["Tech House", "House"],  # "House" unmapped → ignored
        )
        result = clf.classify("Victor Ruiz", "Inception")

        assert result.subgenre == "Tech House"

    def test_confidence_is_ratio_of_winner_to_total(self, full_schema):
        # Tech House: Discogs(3) + Last.fm(2) = 5; Minimal Techno: Discogs(3) = 3
        # Spotify weight is now 0 — does not contribute
        # total = 8, confidence = 5/8
        clf = self._make_classifier(
            full_schema,
            discogs_genres=["Tech House", "Minimal"],
            lastfm_genres=["Tech House"],
            spotify_genres=["Tech House"],
        )
        result = clf.classify("Artist", "Track")

        assert result.subgenre == "Tech House"
        assert abs(result.confidence - 5 / 8) < 1e-6

    def test_no_consensus_when_all_providers_empty(self, full_schema):
        clf = self._make_classifier(
            full_schema,
            discogs_genres=[],
            lastfm_genres=[],
            spotify_genres=[],
        )
        result = clf.classify("Unknown", "Track")

        assert result.subgenre is None
        assert result.confidence == 0.0

    def test_unmapped_genres_are_ignored(self, full_schema):
        # "house", "electronic" are broad and not in alias map → not counted
        clf = self._make_classifier(
            full_schema,
            discogs_genres=["Drum & Bass"],
            lastfm_genres=["electronic", "house"],  # unmapped
            spotify_genres=[],
        )
        result = clf.classify("Chase & Status", "Flashing Lights")

        assert result.subgenre == "Drum & Bass"
        assert result.confidence == 1.0

    def test_evidence_list_contains_all_three_providers(self, full_schema):
        clf = self._make_classifier(
            full_schema,
            discogs_genres=["Tech House"],
            lastfm_genres=[],
            spotify_genres=[],
        )
        result = clf.classify("Artist", "Track")

        provider_names = [ev.provider for ev in result.evidence]
        assert "Discogs" in provider_names
        assert "Last.fm" in provider_names
        assert "Spotify" in provider_names
        assert "MusicBrainz" not in provider_names

    def test_record_label_is_none(self, full_schema):
        clf = self._make_classifier(
            full_schema,
            discogs_genres=["Peak-Time Techno"],
            lastfm_genres=[],
            spotify_genres=[],
        )
        result = clf.classify("Adam Beyer", "Powerdance")

        assert result.record_label is None


# ===========================================================================
# TestBroadGenreFallback
# ===========================================================================

class TestBroadGenreFallback:
    """Broad genre fallback — only fires when no specific subgenre wins."""

    def _make_classifier(self, schema, discogs_genres, lastfm_genres):
        discogs = MagicMock(spec=DiscogsProvider)
        discogs.fetch_evidence.return_value = _make_evidence(
            "Discogs", discogs_genres, _WEIGHT_DISCOGS
        )
        lastfm = MagicMock(spec=LastFmProvider)
        lastfm.fetch_evidence.return_value = _make_evidence(
            "Last.fm", lastfm_genres, _WEIGHT_LASTFM
        )
        spotify = MagicMock(spec=SpotifyProvider)
        spotify.fetch_evidence.return_value = _make_evidence("Spotify", [], 0.0)
        return ConsensusClassifier(
            env={}, schema=schema,
            spotify_provider=spotify, discogs_provider=discogs,
            lastfm_provider=lastfm,
        )

    def test_broad_fires_when_only_house_available(self, full_schema):
        clf = self._make_classifier(full_schema, discogs_genres=["House"], lastfm_genres=[])
        result = clf.classify("Artist", "Track")

        assert result.subgenre == "House"
        assert result.is_broad_fallback is True

    def test_broad_fires_when_only_techno_available(self, full_schema):
        clf = self._make_classifier(full_schema, discogs_genres=[], lastfm_genres=["techno"])
        result = clf.classify("Artist", "Track")

        assert result.subgenre == "Techno"
        assert result.is_broad_fallback is True

    def test_broad_fires_for_trance(self, full_schema):
        clf = self._make_classifier(full_schema, discogs_genres=["Trance"], lastfm_genres=[])
        result = clf.classify("Artist", "Track")

        assert result.subgenre == "Trance"
        assert result.is_broad_fallback is True

    def test_schema_wins_over_broad(self, full_schema):
        """When a specific schema subgenre is found, broad fallback must NOT fire."""
        clf = self._make_classifier(
            full_schema, discogs_genres=["deep house"], lastfm_genres=["house"]
        )
        result = clf.classify("Artist", "Track")

        assert result.subgenre == "Deep House"
        assert result.is_broad_fallback is False

    def test_no_match_returns_none(self, full_schema):
        clf = self._make_classifier(full_schema, discogs_genres=[], lastfm_genres=[])
        result = clf.classify("Unknown", "Track")

        assert result.subgenre is None
        assert result.is_broad_fallback is False

    def test_broad_genre_map_keys_are_lowercase(self):
        """All keys in _BROAD_GENRE_MAP must be normalised (no uppercase, no leading/trailing spaces)."""
        import unicodedata
        for key in _BROAD_GENRE_MAP:
            normalised = " ".join(unicodedata.normalize("NFKC", key).lower().split())
            assert key == normalised, f"_BROAD_GENRE_MAP key {key!r} is not normalised"


# ===========================================================================
# TestClassificationResult
# ===========================================================================

class TestClassificationResult:
    """explain() output format."""

    def test_explain_includes_subgenre_and_confidence(self):
        result = ClassificationResult(
            subgenre="Tech House",
            confidence=0.75,
            evidence=[
                _make_evidence("Discogs", ["Tech House"], _WEIGHT_DISCOGS),
                _make_evidence("Last.fm", ["Tech House", "Minimal"], _WEIGHT_LASTFM),
                _make_evidence("Spotify", [], _WEIGHT_SPOTIFY),
            ],
            record_label="Cocoon Recordings",
        )
        explanation = result.explain()

        assert '"Tech House"' in explanation
        assert "75%" in explanation
        assert "Cocoon Recordings" in explanation
        assert "Discogs" in explanation
        assert "Last.fm" in explanation

    def test_explain_undetermined_when_no_subgenre(self):
        result = ClassificationResult(
            subgenre=None,
            confidence=0.0,
            evidence=[],
            record_label=None,
        )
        explanation = result.explain()

        assert "undetermined" in explanation

    def test_explain_shows_no_genres_for_empty_provider(self):
        result = ClassificationResult(
            subgenre="Minimal Techno",
            confidence=1.0,
            evidence=[_make_evidence("Spotify", [], _WEIGHT_SPOTIFY)],
            record_label=None,
        )
        explanation = result.explain()

        assert "no relevant genres found" in explanation

    def test_explain_marks_broad_fallback(self):
        result = ClassificationResult(
            subgenre="Techno",
            confidence=1.0,
            evidence=[],
            record_label=None,
            is_broad_fallback=True,
        )
        explanation = result.explain()

        assert '"Techno"' in explanation
        assert "broad genre fallback" in explanation
