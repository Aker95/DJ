"""
Integration tests for the consensus classifier against live APIs.

These tests make real network calls to Spotify, Discogs, Last.fm, and
MusicBrainz. They serve two purposes:
  1. Observation — run with -s to print raw provider responses and identify
     which genre strings each API returns before normalisation.
  2. Regression — once the alias map is correctly populated, assert that the
     classifier produces the right subgenre for a known track.

Test track:
  Black Coffee — Superman

Run with:
    pytest tests/integration/test_classifier_live.py -m integration -v -s
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Test subject
# ---------------------------------------------------------------------------

TEST_ARTIST = "Black Coffee"
TEST_TITLE  = "Superman"


# ---------------------------------------------------------------------------
# Module-scoped fixtures (expensive to init — shared across all tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def env():
    from utils.config import load_env
    return load_env()


@pytest.fixture(scope="module")
def schema():
    from utils.config import load_schema
    return load_schema()


@pytest.fixture(scope="module")
def classifier(env, schema):
    from utils.classifier import ConsensusClassifier
    return ConsensusClassifier(env=env, schema=schema)


@pytest.fixture(scope="module")
def spotify_provider(classifier):
    return classifier._spotify


@pytest.fixture(scope="module")
def discogs_provider(classifier):
    return classifier._discogs


@pytest.fixture(scope="module")
def lastfm_provider(classifier):
    return classifier._lastfm


# ---------------------------------------------------------------------------
# TestRawProviderOutputs
#
# These tests intentionally print raw API responses (-s flag required).
# The primary purpose is observation — failing assertions here indicate
# the provider made a successful call but returned unexpected data.
# ---------------------------------------------------------------------------

class TestRawProviderOutputs:

    def test_spotify_returns_genres(self, spotify_provider):
        """Spotify fetches artist-level genres. Prints raw for alias-map observation."""
        ev = spotify_provider.fetch_evidence(TEST_ARTIST, TEST_TITLE)

        print(f"\n[Spotify] raw:    {ev.raw}")
        print(f"[Spotify] genres: {ev.genres}")

        assert isinstance(ev.genres, list), "fetch_evidence must return a list, not raise"
        assert "error" not in ev.raw, f"Spotify provider error: {ev.raw.get('error')}"

    def test_discogs_returns_styles(self, discogs_provider):
        """Discogs fetches release styles. Prints raw for alias-map observation."""
        ev = discogs_provider.fetch_evidence(TEST_ARTIST, TEST_TITLE)

        print(f"\n[Discogs] raw:    {ev.raw}")
        print(f"[Discogs] genres: {ev.genres}")

        assert isinstance(ev.genres, list), "fetch_evidence must return a list, not raise"
        assert "error" not in ev.raw, f"Discogs provider error: {ev.raw.get('error')}"

    def test_lastfm_returns_tags(self, lastfm_provider):
        """Last.fm fetches community tags. Prints raw for alias-map observation."""
        ev = lastfm_provider.fetch_evidence(TEST_ARTIST, TEST_TITLE)

        print(f"\n[Last.fm] raw:    {ev.raw}")
        print(f"[Last.fm] genres: {ev.genres}")

        assert isinstance(ev.genres, list), "fetch_evidence must return a list, not raise"
        assert "error" not in ev.raw, f"Last.fm provider error: {ev.raw.get('error')}"


# ---------------------------------------------------------------------------
# TestFullClassification
#
# End-to-end test. Asserts the classifier produces a valid subgenre.
# Update the `expected_subgenre` assertion once the alias map is populated
# based on the raw output observed above.
# ---------------------------------------------------------------------------

class TestFullClassification:

    def test_classification_is_not_undetermined(self, classifier):
        """Black Coffee — Superman classifies as Deep House via Last.fm artist tags."""
        result = classifier.classify(TEST_ARTIST, TEST_TITLE)

        print(f"\n{result.explain()}")

        assert result.subgenre == "Deep House"

    def test_confidence_is_above_zero(self, classifier):
        """At least one provider must have contributed a mapped genre."""
        result = classifier.classify(TEST_ARTIST, TEST_TITLE)

        print(f"\n[confidence] {result.confidence:.0%}")

        assert result.confidence > 0.0
