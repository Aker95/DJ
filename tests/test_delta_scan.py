"""
Tests for scripts/03_delta_scan.py
"""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import importlib
delta_mod = importlib.import_module("03_delta_scan")

normalize = delta_mod.normalize
clean = delta_mod.clean
preprocess_stem = delta_mod.preprocess_stem
match_scores = delta_mod.match_scores
artist_found_in_stem = delta_mod.artist_found_in_stem
load_local_stems = delta_mod.load_local_stems
is_local_match = delta_mod.is_local_match


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_lowercases(self):
        assert normalize("HELLO WORLD") == "hello world"

    def test_strips_special_chars(self):
        assert normalize("hello, world!") == "hello world"

    def test_strips_hyphens(self):
        # hyphens are stripped; surrounding spaces collapse to one space
        assert normalize("artist - title") == "artist  title".replace("  ", " ")

    def test_collapses_whitespace(self):
        assert normalize("hello   world") == "hello world"

    def test_strips_leading_trailing_whitespace(self):
        assert normalize("  hello  ") == "hello"

    def test_empty_string(self):
        assert normalize("") == ""

    def test_preserves_numbers(self):
        assert normalize("track 4 life") == "track 4 life"

    def test_strips_ampersand(self):
        result = normalize("vintage culture & gabss")
        assert "&" not in result

    def test_strips_parentheses(self):
        result = normalize("lost (original mix)")
        assert "(" not in result
        assert ")" not in result


# ---------------------------------------------------------------------------
# load_local_stems
# ---------------------------------------------------------------------------

class TestLoadLocalStems:
    def test_returns_stems_for_audio_files(self, tmp_path):
        (tmp_path / "Artist A - Track One.mp3").touch()
        (tmp_path / "Artist B - Track Two.flac").touch()
        stems = load_local_stems(tmp_path)
        assert len(stems) == 2

    def test_ignores_non_audio_files(self, tmp_path):
        (tmp_path / "artwork.jpg").touch()
        (tmp_path / "info.txt").touch()
        (tmp_path / "Artist - Track.mp3").touch()
        stems = load_local_stems(tmp_path)
        assert len(stems) == 1

    def test_supports_all_audio_extensions(self, tmp_path):
        for ext in [".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a"]:
            (tmp_path / f"track{ext}").touch()
        stems = load_local_stems(tmp_path)
        assert len(stems) == 6

    def test_stems_are_raw(self, tmp_path):
        (tmp_path / "Black Coffee - Kawa.mp3").touch()
        stems = load_local_stems(tmp_path)
        # stems are now returned raw so the matching pipeline can preprocess them
        assert stems[0] == "Black Coffee - Kawa"

    def test_returns_empty_for_nonexistent_folder(self, tmp_path):
        stems = load_local_stems(tmp_path / "nonexistent")
        assert stems == []

    def test_returns_empty_for_empty_folder(self, tmp_path):
        stems = load_local_stems(tmp_path)
        assert stems == []

    def test_ignores_subdirectories(self, tmp_path):
        (tmp_path / "subfolder").mkdir()
        (tmp_path / "track.mp3").touch()
        stems = load_local_stems(tmp_path)
        assert len(stems) == 1


# ---------------------------------------------------------------------------
# preprocess_stem
# ---------------------------------------------------------------------------

class TestPreprocessStem:
    def test_basic_artist_title_split(self):
        artist, title = preprocess_stem("Black Coffee - Kawa")
        assert artist == "Black Coffee"
        assert title == "Kawa"

    def test_strips_track_number_prefix_with_dash(self):
        artist, title = preprocess_stem("01-vintage culture and gabss - lost")
        assert "01" not in artist

    def test_strips_track_number_prefix_with_space(self):
        artist, title = preprocess_stem("03 Artist Name - Track Title")
        assert "03" not in artist

    def test_strips_underscore_track_num(self):
        artist, title = preprocess_stem("02_Bicep - Glue")
        assert "02" not in artist

    def test_replaces_underscores_with_spaces(self):
        artist, title = preprocess_stem("vintage_culture_and_gabss_-_lost")
        assert "_" not in artist
        assert "_" not in title

    def test_strips_original_mix_suffix(self):
        _, title = preprocess_stem("Artist - Track (Original Mix)")
        assert "original mix" not in title.lower()

    def test_strips_extended_mix_suffix(self):
        _, title = preprocess_stem("Artist - Track (Extended Mix)")
        assert "extended" not in title.lower()

    def test_strips_remix_suffix(self):
        _, title = preprocess_stem("Artist - Track (Someone's Remix)")
        assert "remix" not in title.lower()

    def test_strips_square_bracket_version(self):
        _, title = preprocess_stem("Artist - Track [Club Edit]")
        assert "club" not in title.lower()

    def test_strips_site_prefix_spotidownloader(self):
        artist, title = preprocess_stem("SpotiDownloader.com - Love Story - Victor Ruiz")
        assert "spotidownloader" not in artist.lower()
        assert "spotidownloader" not in title.lower()

    def test_spotidownloader_multi_segment_title_artist_last(self):
        """
        Regression: SpotiDownloader format is 'Title - Artist' (artist last).
        A title containing ' - ' must not be split at the first dash.

        File:  SpotiDownloader.com - Enough to Believe - Jamie Jones Remix - Bob Moses.mp3
        Stem after prefix strip: Enough to Believe - Jamie Jones Remix - Bob Moses
        Expected: artist='Bob Moses', title='Enough to Believe - Jamie Jones Remix'
        Bug was: split(' - ', 1) yielded artist='Enough to Believe', title='Jamie Jones Remix - Bob Moses'
        """
        artist, title = preprocess_stem(
            "SpotiDownloader.com - Enough to Believe - Jamie Jones Remix - Bob Moses"
        )
        assert artist == "Bob Moses"
        assert title == "Enough to Believe - Jamie Jones Remix"

    def test_spotidownloader_three_segment_match_passes_thresholds(self):
        """End-to-end: preprocessed candidates must score above match thresholds."""
        artist, title = preprocess_stem(
            "SpotiDownloader.com - Enough to Believe - Jamie Jones Remix - Bob Moses"
        )
        a, t = match_scores(artist, title, "Bob Moses", "Enough to Believe - Jamie Jones Remix")
        assert a >= 0.80, f"artist score {a:.2f} below threshold"
        assert t >= 0.85, f"title score {t:.2f} below threshold"

    def test_strips_site_prefix_generic(self):
        artist, title = preprocess_stem("Beatport.com - Track Name - Artist Name")
        assert "beatport" not in artist.lower()

    def test_full_real_example_vintage_culture(self):
        """01-vintage_culture_and_gabss_-_lost_(original_mix) → (artist, lost)"""
        artist, title = preprocess_stem("01-vintage_culture_and_gabss_-_lost_(original_mix)")
        assert "lost" in title.lower()
        assert "original" not in title.lower()

    def test_no_separator_returns_empty_artist(self):
        artist, title = preprocess_stem("justasingleword")
        assert artist == ""
        assert title == "justasingleword"

    def test_strips_trailing_dashes_and_spaces(self):
        artist, title = preprocess_stem("  Artist - Title  ")
        assert not artist.startswith(" ")
        assert not title.endswith(" ")


# ---------------------------------------------------------------------------
# match_scores
# ---------------------------------------------------------------------------

class TestMatchScores:
    def test_exact_match_normal_order(self):
        a, t = match_scores("Victor Ruiz", "Love Story", "Victor Ruiz", "Love Story")
        assert a >= 0.99
        assert t >= 0.99

    def test_swapped_order_title_artist_filename(self):
        """
        Filename: "SpotiDownloader.com - Love Story - Victor Ruiz"
        After preprocessing: cand_artist="Love Story", cand_title="Victor Ruiz"
        match_scores must detect the swap and return high scores.
        """
        a, t = match_scores("Love Story", "Victor Ruiz", "Victor Ruiz", "Love Story")
        assert a >= 0.80
        assert t >= 0.85

    def test_enough_to_believe_raw_filename_matches_spotify_metadata(self):
        """
        Real file: SpotiDownloader.com - Enough to Believe - Jamie Jones Remix - Bob Moses.mp3
        Spotify:   title='Enough to Believe - Jamie Jones Remix', artist='Bob Moses'
        """
        raw_stem = "SpotiDownloader.com - Enough to Believe - Jamie Jones Remix - Bob Moses"
        spotify_title = "Enough to Believe - Jamie Jones Remix"
        spotify_artist = "Bob Moses"

        cand_artist, cand_title = preprocess_stem(raw_stem)
        a_score, t_score = match_scores(cand_artist, cand_title, spotify_artist, spotify_title)

        assert cand_artist == "Bob Moses", (
            f"Expected artist 'Bob Moses', got '{cand_artist}'. "
            "preprocess_stem is splitting at the wrong ' - '."
        )
        assert cand_title == "Enough to Believe - Jamie Jones Remix", (
            f"Expected title 'Enough to Believe - Jamie Jones Remix', got '{cand_title}'."
        )
        assert a_score >= 0.80, f"artist score {a_score:.2f} below 0.80 threshold"
        assert t_score >= 0.85, f"title score {t_score:.2f} below 0.85 threshold"

    def test_parenthetical_mix_suffix_stripped_spotify_retains_base_title(self):
        """
        Real file: Lost Frequencies - Rise (Extended Deluxe Mix).mp3
        Spotify:   title='Rise - Deluxe Mix', artist='Lost Frequencies'
        preprocess_stem strips '(Extended Deluxe Mix)' → cand_title='Rise'
        token_set_ratio('rise', 'rise deluxe mix') returns 100 because 'rise'
        is a perfect subset of 'rise deluxe mix'.
        """
        raw_stem = "Lost Frequencies - Rise (Extended Deluxe Mix)"
        cand_artist, cand_title = preprocess_stem(raw_stem)
        a_score, t_score = match_scores(cand_artist, cand_title, "Lost Frequencies", "Rise - Deluxe Mix")
        assert a_score >= 0.80, f"artist score {a_score:.2f} below 0.80 threshold"
        assert t_score >= 0.85, f"title score {t_score:.2f} below 0.85 threshold"

    def test_completely_different_returns_low_scores(self):
        a, t = match_scores("DJ Koze", "Pick Up", "Victor Ruiz", "Love Story")
        assert a + t < 1.0

    def test_case_insensitive_matching(self):
        a, t = match_scores("black coffee", "kawa", "Black Coffee", "Kawa")
        assert a >= 0.90
        assert t >= 0.90

    def test_partial_match_artist_variation(self):
        """'Vintage Culture and Gabss' vs 'Vintage Culture & Gabss'"""
        a, t = match_scores(
            "Vintage Culture and Gabss", "Lost",
            "Vintage Culture Gabss", "Lost"
        )
        assert a >= 0.80
        assert t >= 0.95

    def test_returns_best_ordering(self):
        """Result should always favour the higher-scoring field ordering."""
        normal_a, normal_t = match_scores("A", "B", "B", "A")
        assert normal_a >= 0.80

    def test_real_file_steve_angello_show_me_love(self):
        """
        Real file in workspace:
        music/DJ PEAK HOUSE - CLUB/electro house/
          Steve Angello & Laidback Luke Feat. Robin S. - Show Me Love (Extended Mix).aiff
        Spotify: artist='Steve Angello', title='Show Me Love'
        """
        raw_stem = "Steve Angello & Laidback Luke Feat. Robin S. - Show Me Love (Extended Mix)"
        cand_artist, cand_title = preprocess_stem(raw_stem)
        a_score, t_score = match_scores(cand_artist, cand_title, "Steve Angello", "Show Me Love")
        assert a_score >= 0.80, f"artist score {a_score:.2f} below 0.80 threshold"
        assert t_score >= 0.85, f"title score {t_score:.2f} below 0.85 threshold"


# ---------------------------------------------------------------------------
# artist_found_in_stem
# ---------------------------------------------------------------------------

class TestArtistFoundInStem:
    def test_artist_found_when_buried_in_multi_artist_stem(self):
        """'Laidback Luke' is present in the raw stem even though it wasn't
        parsed as cand_artist (because it follows '&')."""
        stem = "Steve Angello & Laidback Luke Feat. Robin S. - Show Me Love (Extended Mix)"
        assert artist_found_in_stem("Laidback Luke", stem) is True

    def test_artist_found_after_track_number(self):
        """'Rachel Row' is in the stem despite a 4-digit fake track number prefix."""
        stem = "1491 - Rachel Row - Follow The Step (Justin Martin Remix)"
        assert artist_found_in_stem("Rachel Row", stem) is True

    def test_artist_found_in_underscore_stem(self):
        """Artist embedded in an all-underscore filename with no dash separator."""
        stem = "Duke Dumont_Duality_08_Love Song"
        assert artist_found_in_stem("Duke Dumont", stem) is True

    def test_artist_not_in_stem_returns_false(self):
        """'Daft Punk' does not appear in '07 Around the World'."""
        stem = "07 Around the World"
        assert artist_found_in_stem("Daft Punk", stem) is False

    def test_empty_ref_artist_returns_false(self):
        assert artist_found_in_stem("", "Some Artist - Some Track") is False




class TestIsLocalMatch:
    def test_exact_match(self, tmp_path):
        (tmp_path / "Black Coffee - Kawa.mp3").touch()
        stems = load_local_stems(tmp_path)
        assert is_local_match(stems, "Black Coffee", "Kawa")

    def test_no_match(self, tmp_path):
        (tmp_path / "Black Coffee - Kawa.mp3").touch()
        stems = load_local_stems(tmp_path)
        assert not is_local_match(stems, "Victor Ruiz", "Love Story")

    def test_case_insensitive_match(self, tmp_path):
        (tmp_path / "black coffee - kawa.mp3").touch()
        stems = load_local_stems(tmp_path)
        assert is_local_match(stems, "BLACK COFFEE", "KAWA")

    def test_minor_variation_matches(self, tmp_path):
        # "Enoo Napa" vs "enoo napa" slight punctuation difference
        (tmp_path / "Enoo Napa - Superman.mp3").touch()
        stems = load_local_stems(tmp_path)
        assert is_local_match(stems, "Enoo Napa", "Superman")

    def test_empty_folder_no_match(self):
        assert not is_local_match([], "Artist", "Title")

    def test_threshold_respected(self, tmp_path):
        # Completely different track should not match even below threshold
        (tmp_path / "DJ Koze - Pick Up.flac").touch()
        stems = load_local_stems(tmp_path)
        assert not is_local_match(stems, "Victor Ruiz", "Love Story")

    def test_ampersand_vs_and_matches(self, tmp_path):
        # "Vintage Culture & Gabss" vs "Vintage Culture and Gabss" should fuzzy-match
        (tmp_path / "Vintage Culture & Gabss - Lost.mp3").touch()
        stems = load_local_stems(tmp_path)
        assert is_local_match(stems, "Vintage Culture and Gabss", "Lost")


# ---------------------------------------------------------------------------
# main() — integration
# ---------------------------------------------------------------------------

class TestDeltaScanMain:
    def _run(self, tmp_path, snapshot, music_root):
        snapshot_path = tmp_path / "spotify_snapshot.json"
        delta_path = tmp_path / "delta.json"
        snapshot_path.write_text(json.dumps(snapshot))

        # Build a minimal schema that maps each snapshot subgenre to its flat local_folder
        fake_schema = {"master_folders": [{"name": "test", "subgenres": [
            {"name": pl["schema_subgenre"], "local_folder": pl["local_folder"]}
            for pl in snapshot["playlists"]
        ]}]}

        with (
            patch.object(delta_mod, "PROJECT_ROOT", tmp_path),
            patch.object(delta_mod, "SNAPSHOT_PATH", snapshot_path),
            patch.object(delta_mod, "DELTA_PATH", delta_path),
            patch.object(delta_mod, "load_env", return_value={"MUSIC_ROOT": str(music_root)}),
            patch.object(delta_mod, "load_schema", return_value=fake_schema),
        ):
            delta_mod.main()

        return json.loads(delta_path.read_text())

    def test_correctly_identifies_missing_tracks(self, tmp_path, sample_snapshot):
        music_root = tmp_path / "music"
        (music_root / "afro house").mkdir(parents=True)
        (music_root / "tech house").mkdir(parents=True)
        # Place only one of the afro house tracks locally
        (music_root / "afro house" / "Black Coffee - Kawa.mp3").touch()

        delta = self._run(tmp_path, sample_snapshot, music_root)

        artists = [t["artist"] for t in delta["tracks"]]
        assert "Black Coffee" not in artists   # present locally
        assert "Enoo Napa" in artists          # missing
        assert "Artist B" in artists           # missing

    def test_all_tracks_missing_when_folder_empty(self, tmp_path, sample_snapshot):
        music_root = tmp_path / "music"
        (music_root / "afro house").mkdir(parents=True)
        (music_root / "tech house").mkdir(parents=True)

        delta = self._run(tmp_path, sample_snapshot, music_root)
        assert delta["total_missing"] == 5  # all tracks in sample_snapshot

    def test_no_delta_when_all_present(self, tmp_path, sample_snapshot):
        music_root = tmp_path / "music"
        (music_root / "afro house").mkdir(parents=True)
        (music_root / "tech house").mkdir(parents=True)
        # Create all tracks as local files
        for pl in sample_snapshot["playlists"]:
            folder = music_root / pl["local_folder"].split("/", 1)[1]
            for t in pl["tracks"]:
                (folder / f"{t['artist']} - {t['title']}.mp3").touch()

        delta = self._run(tmp_path, sample_snapshot, music_root)
        assert delta["total_missing"] == 0
        assert delta["tracks"] == []

    def test_delta_track_has_required_fields(self, tmp_path, sample_snapshot):
        music_root = tmp_path / "music"
        (music_root / "afro house").mkdir(parents=True)
        (music_root / "tech house").mkdir(parents=True)

        delta = self._run(tmp_path, sample_snapshot, music_root)
        for track in delta["tracks"]:
            assert "artist" in track
            assert "title" in track
            assert "spotify_id" in track
            assert "subgenre" in track
            assert "master_folder" in track
            assert "local_folder" in track

    def test_exits_if_snapshot_missing(self, tmp_path):
        with (
            patch.object(delta_mod, "PROJECT_ROOT", tmp_path),
            patch.object(delta_mod, "SNAPSHOT_PATH", tmp_path / "nonexistent.json"),
            patch.object(delta_mod, "load_env", return_value={"MUSIC_ROOT": str(tmp_path / "music")}),
        ):
            with pytest.raises(SystemExit):
                delta_mod.main()

    def test_handles_null_local_folder(self, tmp_path):
        """Playlists with local_folder: null should derive path from subgenre name."""
        snapshot = {
            "generated_at": "2026-06-24T00:00:00+00:00",
            "total_playlists": 1,
            "playlists": [{
                "playlist_id": "p1",
                "playlist_name": "Melodic House",
                "schema_subgenre": "Melodic House",
                "master_folder": "DJ CHILL / GROOVE",
                "local_folder": None,
                "tracks": [{"id": "t1", "title": "Sunrise", "artist": "Lane 8", "duration_ms": 300000}],
            }],
        }
        music_root = tmp_path / "music"
        music_root.mkdir()

        delta = self._run(tmp_path, snapshot, music_root)
        assert delta["total_missing"] == 1
        assert delta["tracks"][0]["artist"] == "Lane 8"

    def test_writes_generated_at_timestamp(self, tmp_path, sample_snapshot):
        music_root = tmp_path / "music"
        (music_root / "afro house").mkdir(parents=True)
        (music_root / "tech house").mkdir(parents=True)

        delta = self._run(tmp_path, sample_snapshot, music_root)
        assert "generated_at" in delta
        assert "T" in delta["generated_at"]  # ISO format
