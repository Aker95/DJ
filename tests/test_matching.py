"""
Tests for scripts/utils/matching.py

Covers every public function:
  normalize, clean, preprocess_stem, sig_words,
  match_scores, artist_found_in_stem, filename_matches
"""

import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from utils.matching import (
    normalize,
    clean,
    preprocess_stem,
    sig_words,
    match_scores,
    artist_found_in_stem,
    filename_matches,
)


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_lowercases(self):
        assert normalize("HELLO WORLD") == "hello world"

    def test_strips_punctuation(self):
        assert normalize("hello, world!") == "hello world"

    def test_collapses_whitespace(self):
        assert normalize("hello   world") == "hello world"

    def test_strips_leading_trailing_whitespace(self):
        assert normalize("  hello  ") == "hello"

    def test_empty_string(self):
        assert normalize("") == ""

    def test_preserves_numbers(self):
        assert normalize("track 4 life") == "track 4 life"

    def test_unicode_nfkd_accents(self):
        """Tiësto → tiesto via NFKD decomposition."""
        assert normalize("Tiësto") == "tiesto"

    def test_unicode_nfkd_typographic_dash(self):
        """Em-dash becomes space (then collapsed)."""
        result = normalize("artist\u2014title")
        assert "artist" in result and "title" in result

    def test_strips_ampersand(self):
        assert "&" not in normalize("vintage culture & gabss")

    def test_strips_parentheses(self):
        result = normalize("lost (original mix)")
        assert "(" not in result and ")" not in result


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------

class TestClean:
    # Generic parenthetical forms — must be stripped
    def test_strips_original_mix_parens(self):
        assert "original" not in clean("Running Away (Original Mix)")
        assert "mix" not in clean("Running Away (Original Mix)")

    def test_strips_extended_mix_parens(self):
        assert "extended" not in clean("Lucky Strike (Extended Mix)")

    def test_strips_radio_edit_parens(self):
        assert "radio" not in clean("Track (Radio Edit)")

    def test_strips_club_mix_parens(self):
        assert "club" not in clean("Track (Club Mix)")

    def test_strips_vip_mix_parens(self):
        assert "vip" not in clean("Track (VIP Mix)")

    def test_strips_instrumental_parens(self):
        assert "instrumental" not in clean("Track (Instrumental)")

    def test_strips_remaster_parens(self):
        assert "remaster" not in clean("Track (Remaster)")

    def test_strips_remastered_parens(self):
        assert "remaster" not in clean("Track (Remastered)")

    def test_strips_bonus_track_parens(self):
        assert "bonus" not in clean("Track (Bonus Track)")

    def test_strips_bootleg_parens(self):
        assert "bootleg" not in clean("Track (Bootleg)")

    def test_strips_extended_deluxe_mix_parens(self):
        """Multi-word generic suffix: all words in the vocabulary."""
        result = clean("Rise (Extended Deluxe Mix)")
        assert "extended" not in result
        assert "deluxe" not in result
        assert "rise" in result

    def test_strips_square_bracket_version(self):
        result = clean("Track [Club Edit]")
        assert "club" not in result

    # Generic dash-suffix forms — must be stripped
    def test_strips_original_mix_dash(self):
        assert "original" not in clean("Area 91 - Original Mix")
        assert "mix" not in clean("Area 91 - Original Mix")

    def test_strips_radio_edit_dash(self):
        assert "radio" not in clean("Aiming For The Head - Radio Edit")

    def test_strips_extended_mix_dash(self):
        assert "extended" not in clean("Fire II - Extended Mix")

    def test_strips_remaster_dash(self):
        assert "remaster" not in clean("Track - Remastered")

    # Named remixes — must be PRESERVED
    def test_preserves_named_remix_parens(self):
        """(Jamie Jones Remix) is a named remix — must not be stripped."""
        result = clean("Spiders (Jamie Jones Remix)")
        assert "jamie" in result
        assert "remix" in result

    def test_preserves_named_remix_dash(self):
        """- Victor Ruiz Remix is a named remix — must not be stripped."""
        result = clean("Fangbanger - Victor Ruiz Remix")
        assert "victor" in result
        assert "ruiz" in result

    def test_preserves_raumm_remix_dash(self):
        result = clean("Honey - RAUMM Remix")
        assert "raumm" in result

    def test_preserves_kaacee_remix_dash(self):
        result = clean("Wave - KaaCee Remix")
        assert "kaacee" in result

    def test_preserves_joris_voorn_remix_dash(self):
        result = clean("Access - Joris Voorn Remix")
        assert "joris" in result

    def test_preserves_geo22_edit_parens(self):
        """(Geo22 Edit) — proper name before Edit, keep it."""
        result = clean("Lady of the Moon (Geo22 Edit)")
        assert "geo22" in result

    # Feat stripping
    def test_strips_feat_tag(self):
        result = clean("Say Goodbye (feat. EUROPE DJ)")
        assert "europe" not in result

    def test_strips_ft_tag(self):
        result = clean("Track ft. Someone")
        assert "someone" not in result

    # Base title preserved after stripping
    def test_base_title_preserved(self):
        assert "running away" in clean("Running Away (Original Mix)")

    def test_year_paren_stripped(self):
        assert "2024" not in clean("Track (2024)")

    def test_format_tag_stripped(self):
        assert "flac" not in clean("Track [FLAC]")


# ---------------------------------------------------------------------------
# preprocess_stem
# ---------------------------------------------------------------------------

class TestPreprocessStem:
    def test_basic_artist_title_split(self):
        artist, title = preprocess_stem("Black Coffee - Kawa")
        assert artist == "Black Coffee"
        assert title == "Kawa"

    def test_strips_track_number_prefix_dash(self):
        artist, _ = preprocess_stem("01-Artist - Track")
        assert "01" not in artist

    def test_strips_track_number_prefix_space(self):
        artist, _ = preprocess_stem("03 Artist - Track")
        assert "03" not in artist

    def test_strips_track_number_prefix_underscore(self):
        artist, _ = preprocess_stem("02_Bicep - Glue")
        assert "02" not in artist

    def test_replaces_underscores_with_spaces(self):
        artist, title = preprocess_stem("vintage_culture_-_lost")
        assert "_" not in artist
        assert "_" not in title

    def test_strips_original_mix_parens(self):
        _, title = preprocess_stem("Artist - Track (Original Mix)")
        assert "original" not in title.lower()
        assert "mix" not in title.lower()

    def test_strips_extended_mix_parens(self):
        _, title = preprocess_stem("Artist - Track (Extended Mix)")
        assert "extended" not in title.lower()

    def test_strips_club_edit_brackets(self):
        _, title = preprocess_stem("Artist - Track [Club Edit]")
        assert "club" not in title.lower()

    def test_preserves_named_remix_parens(self):
        """(Jamie Jones Remix) is a named remix and must be preserved."""
        _, title = preprocess_stem("Bob Moses - Enough to Believe (Jamie Jones Remix)")
        assert "jamie" in title.lower()
        assert "jones" in title.lower()

    def test_strips_site_prefix_spotidownloader(self):
        artist, title = preprocess_stem("SpotiDownloader.com - Love Story - Victor Ruiz")
        assert "spotidownloader" not in artist.lower()
        assert "spotidownloader" not in title.lower()

    def test_spotidownloader_artist_is_last_segment(self):
        """SpotiDownloader: artist is always the last dash-segment."""
        artist, title = preprocess_stem(
            "SpotiDownloader.com - Enough to Believe - Jamie Jones Remix - Bob Moses"
        )
        assert artist == "Bob Moses"
        assert title == "Enough to Believe - Jamie Jones Remix"

    def test_strips_generic_site_prefix(self):
        artist, _ = preprocess_stem("Beatport.com - Track Name - Artist Name")
        assert "beatport" not in artist.lower()

    def test_no_separator_returns_empty_artist(self):
        artist, title = preprocess_stem("justasingleword")
        assert artist == ""
        assert title == "justasingleword"

    def test_real_example_vintage_culture(self):
        artist, title = preprocess_stem("01-vintage_culture_and_gabss_-_lost_(original_mix)")
        assert "lost" in title.lower()
        assert "original" not in title.lower()

    def test_real_example_steve_angello(self):
        artist, title = preprocess_stem(
            "Steve Angello & Laidback Luke Feat. Robin S. - Show Me Love (Extended Mix)"
        )
        assert "angello" in artist.lower()
        assert "extended" not in title.lower()


# ---------------------------------------------------------------------------
# sig_words
# ---------------------------------------------------------------------------

class TestSigWords:
    def test_filters_short_words(self):
        assert "a" not in sig_words("a big track")
        assert "of" not in sig_words("track of life")

    def test_filters_stopwords(self):
        words = sig_words("the artist and the title")
        assert "the" not in words
        assert "and" not in words

    def test_keeps_significant_words(self):
        words = sig_words("Victor Ruiz Lucky Strike")
        assert "victor" in words
        assert "lucky" in words
        assert "strike" in words

    def test_empty_string(self):
        assert sig_words("") == []

    def test_normalizes_unicode(self):
        words = sig_words("Tiësto")
        assert "tiesto" in words


# ---------------------------------------------------------------------------
# match_scores
# ---------------------------------------------------------------------------

class TestMatchScores:
    def test_exact_match(self):
        a, t = match_scores("Victor Ruiz", "Love Story", "Victor Ruiz", "Love Story")
        assert a >= 0.99
        assert t >= 0.99

    def test_swapped_order(self):
        """Handles title-as-artist, artist-as-title filename ordering."""
        a, t = match_scores("Love Story", "Victor Ruiz", "Victor Ruiz", "Love Story")
        assert a >= 0.80
        assert t >= 0.85

    def test_completely_different_low_scores(self):
        a, t = match_scores("DJ Koze", "Pick Up", "Victor Ruiz", "Love Story")
        assert a + t < 1.0

    def test_case_insensitive(self):
        a, t = match_scores("black coffee", "kawa", "Black Coffee", "Kawa")
        assert a >= 0.90
        assert t >= 0.90

    def test_strips_generic_version_before_scoring(self):
        """clean() strips (Original Mix) from both sides before scoring."""
        a, t = match_scores(
            "Joyhauser", "Running Away",
            "Joyhauser", "Running Away (Original Mix)",
        )
        assert t >= 0.99

    def test_preserves_named_remix_for_scoring(self):
        """Named remix signal is retained, so score against the full Spotify title."""
        a, t = match_scores(
            "Bob Moses", "Enough to Believe - Jamie Jones Remix",
            "Bob Moses", "Enough to Believe - Jamie Jones Remix",
        )
        assert a >= 0.99
        assert t >= 0.99

    def test_rise_deluxe_mix_matches_base_title(self):
        """
        File stem: 'Lost Frequencies - Rise (Extended Deluxe Mix)'
        Spotify title: 'Rise - Deluxe Mix'
        After preprocess_stem → cand_title='Rise'; clean('Rise - Deluxe Mix') → 'rise'
        token_set_ratio('rise', 'rise') = 1.0
        """
        cand_artist, cand_title = preprocess_stem("Lost Frequencies - Rise (Extended Deluxe Mix)")
        a, t = match_scores(cand_artist, cand_title, "Lost Frequencies", "Rise - Deluxe Mix")
        assert a >= 0.80
        assert t >= 0.85

    def test_ampersand_artist_matches(self):
        """'Vintage Culture & Gabss' vs 'Vintage Culture and Gabss' — both normalize to same base."""
        a, t = match_scores(
            "Vintage Culture and Gabss", "Lost",
            "Vintage Culture Gabss", "Lost",
        )
        assert a >= 0.80
        assert t >= 0.95

    def test_spotidownloader_enough_to_believe(self):
        """Regression: full end-to-end with SpotiDownloader stem."""
        raw_stem = "SpotiDownloader.com - Enough to Believe - Jamie Jones Remix - Bob Moses"
        cand_artist, cand_title = preprocess_stem(raw_stem)
        assert cand_artist == "Bob Moses"
        assert cand_title == "Enough to Believe - Jamie Jones Remix"
        a, t = match_scores(cand_artist, cand_title, "Bob Moses", "Enough to Believe - Jamie Jones Remix")
        assert a >= 0.80
        assert t >= 0.85


# ---------------------------------------------------------------------------
# artist_found_in_stem
# ---------------------------------------------------------------------------

class TestArtistFoundInStem:
    def test_artist_found_in_multi_artist_stem(self):
        stem = "Steve Angello & Laidback Luke Feat. Robin S. - Show Me Love (Extended Mix)"
        assert artist_found_in_stem("Laidback Luke", stem) is True

    def test_artist_found_after_track_number(self):
        stem = "1491 - Rachel Row - Follow The Step (Justin Martin Remix)"
        assert artist_found_in_stem("Rachel Row", stem) is True

    def test_artist_not_in_stem_returns_false(self):
        assert artist_found_in_stem("Daft Punk", "07 Around the World") is False

    def test_empty_artist_returns_false(self):
        assert artist_found_in_stem("", "Some Artist - Some Track") is False


# ---------------------------------------------------------------------------
# filename_matches  (the three-stage gate)
# ---------------------------------------------------------------------------

class TestFilenameMatches:
    # Stage 0 — regex fast path
    def test_stage0_exact_words_in_stem(self):
        """All significant title words + artist word present → Stage 0 True."""
        assert filename_matches(
            "Victor Ruiz - Lucky Strike (Extended Mix).flac",
            "Victor Ruiz", "Lucky Strike",
        )

    def test_stage0_reversed_order_accepted(self):
        assert filename_matches(
            "Lucky Strike - Victor Ruiz.flac",
            "Victor Ruiz", "Lucky Strike",
        )

    # Stage 1 — fuzzy
    def test_stage1_fuzzy_match(self):
        assert filename_matches(
            "Joyhauser - Running Away (Original Mix).flac",
            "Joyhauser", "Running Away",
        )

    def test_stage1_multi_artist_file(self):
        assert filename_matches(
            "Mark Reeve, Joyhauser - Focus (Original Mix).flac",
            "Mark Reeve", "Focus",
        )

    def test_stage1_title_first_artist_last(self):
        assert filename_matches(
            "Miami Vice (Original Mix) - Timmo.flac",
            "Timmo", "Miami Vice",
        )

    # Named remix in delta title
    def test_named_remix_in_title_matches_file(self):
        """Spotify title includes named remix; file must match."""
        assert filename_matches(
            "Thomas Schumacher - Fangbanger (Victor Ruiz Remix).flac",
            "Thomas Schumacher", "Fangbanger - Victor Ruiz Remix",
        )

    def test_named_remix_dash_in_filename_matches(self):
        assert filename_matches(
            "DJ Misjah - Access (Joris Voorn Remix).flac",
            "DJ Misjah", "Access - Joris Voorn Remix",
        )

    @pytest.mark.xfail(
        strict=True,
        reason="Known bug: an unrequested named remix must not match the Spotify version",
    )
    def test_rejects_unrequested_named_remix_for_base_spotify_title(self):
        """Regression: do not acquire a named remix when Spotify requests the base title."""
        assert not filename_matches(
            "Tiësto - Adagio for Strings (Danjo & Styles Remix).flac",
            "Tiësto", "Adagio for Strings",
        )

    # Rejection — wrong track
    def test_rejects_completely_wrong_file(self):
        """'02. Easy Riders' must not match Victor Ruiz / Lucky Strike."""
        assert not filename_matches(
            "02. Easy Riders.flac",
            "Victor Ruiz", "Lucky Strike",
        )

    def test_rejects_different_artist_same_title_word(self):
        assert not filename_matches(
            "DJ Koze - Pick Up.flac",
            "Victor Ruiz", "Love Story",
        )

    # Gate skipped at pick_best_file level (not inside filename_matches)
    def test_empty_artist_returns_false(self):
        """filename_matches with empty artist still tries Stage 0/1/2; no shortcut."""
        # With empty artist, stage 0 needs all title words in stem but stem is "random track"
        assert not filename_matches("random track.flac", "", "Lucky Strike")

    def test_empty_title_returns_false(self):
        """filename_matches with empty title returns False (no title words to match)."""
        assert not filename_matches("random track.flac", "Victor Ruiz", "")

    # Windows-style backslash paths
    def test_handles_windows_backslash_path(self):
        assert filename_matches(
            "Download\\Complete\\Victor Ruiz - Lucky Strike.flac",
            "Victor Ruiz", "Lucky Strike",
        )
