from __future__ import annotations

import pytest

from dj_library.matching import (
    artist_found_in_stem,
    clean,
    filename_matches,
    match_scores,
    normalize,
    preprocess_stem,
    significant_words,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Tiësto — Adagio", "tiesto adagio"),
        ("A&B (Live)", "ab live"),
        ("  MULTI   SPACE ", "multi space"),
    ],
)
def test_normalize(value, expected):
    assert normalize(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Track (Original Mix)", "track"),
        ("Track - Extended Mix", "track"),
        ("Track (Jamie Jones Remix)", "track jamie jones remix"),
        ("Track [2024] [FLAC]", "track"),
    ],
)
def test_clean_generic_versions(value, expected):
    assert clean(value) == expected


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("01 Artist - Title (Original Mix)", ("Artist", "Title")),
        ("SpotiDownloader.com - Title - Artist", ("Artist", "Title")),
        ("No Separator", ("", "No Separator")),
        ("03_Artist - Named Remix (DJ X Remix)", ("Artist", "Named Remix (DJ X Remix)")),
    ],
)
def test_preprocess_stem(stem, expected):
    assert preprocess_stem(stem) == expected


def test_significant_words_filters_noise():
    assert significant_words("The Sound of Love") == ["sound", "love"]


def test_match_scores_accepts_swapped_fields():
    assert match_scores("Title", "Artist", "Artist", "Title") == (1, 1)


def test_artist_found_in_stem_normalizes_unicode():
    assert artist_found_in_stem("Tiësto", "01_Tiesto_-_Adagio")


@pytest.mark.parametrize(
    ("filename", "artist", "title"),
    [
        ("Artist - Title (Extended Mix).flac", "Artist", "Title"),
        ("SpotiDownloader.com - Title - Artist.mp3", "Artist", "Title"),
        (r"folder\Artist - Title.wav", "Artist", "Title"),
    ],
)
def test_filename_matches_valid_variants(filename, artist, title):
    assert filename_matches(filename, artist, title)


def test_filename_rejects_wrong_artist():
    assert not filename_matches("Someone - Title.mp3", "Artist", "Title")


def test_filename_rejects_empty_identity():
    assert not filename_matches("Artist - Title.mp3", "", "Title")
