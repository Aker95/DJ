"""
Tests for scripts/03b_reconcile_unsorted.py

Covers:
  - preprocess_stem: track numbers, site prefixes, mix suffixes, underscores
  - normalize
  - match_scores: normal order, swapped order (title-artist filenames)
  - get_schema_folders
  - main(): file discovery, matching, moving, delta.json update
"""

import json
import sys
import shutil
import pytest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import importlib
recon_mod = importlib.import_module("03b_reconcile_unsorted")

preprocess_stem = recon_mod.preprocess_stem
normalize = recon_mod.normalize
match_scores = recon_mod.match_scores
artist_found_in_stem = recon_mod.artist_found_in_stem
get_schema_folders = recon_mod.get_schema_folders


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
        assert "Original Mix" not in title
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
        A title containing ` - ` must not be split at the first dash.

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
        """
        End-to-end: the preprocessed candidates must score above match thresholds
        against the Spotify delta track so the file gets reconciled.
        """
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
# normalize
# ---------------------------------------------------------------------------

class TestNormalizeRecon:
    def test_lowercases(self):
        assert normalize("HELLO") == "hello"

    def test_strips_special_chars(self):
        assert "&" not in normalize("Vintage Culture & Gabss")

    def test_collapses_whitespace(self):
        assert normalize("a  b") == "a  b".replace("  ", " ").strip() or normalize("a  b") == "a b"

    def test_strips_punctuation(self):
        assert normalize("don't") == "dont"


# ---------------------------------------------------------------------------
# match_scores — normal order and swapped order
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
        Delta: artist="Victor Ruiz", title="Love Story"
        match_scores must detect the swap and return high scores.
        """
        a, t = match_scores("Love Story", "Victor Ruiz", "Victor Ruiz", "Love Story")
        assert a >= 0.80
        assert t >= 0.85

    def test_enough_to_believe_raw_filename_matches_spotify_metadata(self):
        """
        Real file:    music/Set House Play Around/
                      SpotiDownloader.com - Enough to Believe - Jamie Jones Remix - Bob Moses.mp3
        Spotify meta: title='Enough to Believe - Jamie Jones Remix', artist='Bob Moses'
                      (playlist: tech house, id: 4LtVrrqpn48l4Iq7KIshmi)

        This was a live regression: the file existed on disk but was reported missing
        because split(' - ', 1) extracted the wrong artist/title from the stem.
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

    def test_parenthetical_mix_suffix_stripped_but_spotify_retains_base_mix_name(self):
        """
        Regression: file has mix info in parentheses; Spotify title retains a shorter
        version of it after ' - '.

        File:    music/Set House Play Around/Lost Frequencies - Rise (Extended Deluxe Mix).mp3
        Spotify: title='Rise - Deluxe Mix', artist='Lost Frequencies'
                 (playlist: melodic house, id: 0M6IvnUZgo1In9jbmMnM00)

        Failure path:
          preprocess_stem strips '(Extended Deluxe Mix)' → cand_title='Rise'
          sim('rise', 'rise deluxe mix') ≈ 0.44  →  below 0.85 threshold
        Expected fix: also compare cand_title against the base of the Spotify title
          (everything before the first ' - '), so 'rise' vs 'rise' scores 1.0.
        """
        raw_stem = "Lost Frequencies - Rise (Extended Deluxe Mix)"
        spotify_artist = "Lost Frequencies"
        spotify_title = "Rise - Deluxe Mix"

        cand_artist, cand_title = preprocess_stem(raw_stem)
        a_score, t_score = match_scores(cand_artist, cand_title, spotify_artist, spotify_title)

        assert a_score >= 0.80, f"artist score {a_score:.2f} below 0.80 threshold"
        assert t_score >= 0.85, (
            f"title score {t_score:.2f} below 0.85 threshold. "
            f"cand_title='{cand_title}', spotify_title='{spotify_title}'. "
            "match_scores should also compare against the base title 'Rise' (before ' - Deluxe Mix')."
        )

    def test_completely_different_returns_low_scores(self):
        a, t = match_scores("DJ Koze", "Pick Up", "Victor Ruiz", "Love Story")
        assert a + t < 1.0  # combined score is low

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
        """Result should always be >= the non-swapped score."""
        normal_a, normal_t = match_scores("A", "B", "B", "A")
        assert normal_a >= 0.80  # swapped order should win


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
        """'Daft Punk' does not appear in '07 Around the World' — expected edge case."""
        stem = "07 Around the World"
        assert artist_found_in_stem("Daft Punk", stem) is False

    def test_empty_ref_artist_returns_false(self):
        assert artist_found_in_stem("", "Some Artist - Some Track") is False

class TestGetSchemaFolders:
    def test_returns_resolved_paths(self, tmp_path, sample_schema):
        folders = get_schema_folders(sample_schema)
        # Should contain resolved absolute paths
        for p in folders:
            assert isinstance(p, Path)
            assert p.is_absolute()

    def test_excludes_null_local_folders(self, sample_schema):
        folders = get_schema_folders(sample_schema)
        # "Melodic House" has local_folder: null → should not be in schema_folders
        folder_names = {p.name for p in folders}
        assert "melodic house" not in folder_names

    def test_includes_all_non_null_folders(self, sample_schema):
        folders = get_schema_folders(sample_schema)
        folder_names = {p.name for p in folders}
        assert "afro house" in folder_names
        assert "deep house" in folder_names
        assert "tech house" in folder_names
        assert "bass house" in folder_names

    def test_empty_schema_returns_empty_set(self):
        folders = get_schema_folders({"master_folders": []})
        assert folders == set()


# ---------------------------------------------------------------------------
# main() — file matching and delta update
# ---------------------------------------------------------------------------

class TestReconcileMain:
    def _setup(self, tmp_path, sample_schema, sample_delta, unsorted_files: dict, schema_files: dict = None):
        """
        unsorted_files: {folder_name: [filename, ...]} — files in non-schema folders
        schema_files:   {folder_name: [filename, ...]} — files in schema folders (default empty)
        """
        music_root = tmp_path / "music"
        music_root.mkdir()

        # Create schema folders
        for folder in sample_schema["master_folders"]:
            for sg in folder["subgenres"]:
                lf = sg.get("local_folder")
                if lf:
                    (tmp_path / lf).mkdir(parents=True, exist_ok=True)

        # Populate schema folders if requested
        for folder_name, files in (schema_files or {}).items():
            folder = tmp_path / "music" / folder_name
            folder.mkdir(parents=True, exist_ok=True)
            for fname in files:
                (folder / fname).touch()

        # Create and populate unsorted folders
        for folder_name, files in unsorted_files.items():
            folder = music_root / folder_name
            folder.mkdir(parents=True, exist_ok=True)
            for fname in files:
                (folder / fname).touch()

        delta_path = tmp_path / "delta.json"
        delta_path.write_text(json.dumps(sample_delta))

        with (
            patch.object(recon_mod, "PROJECT_ROOT", tmp_path),
            patch.object(recon_mod, "DELTA_PATH", delta_path),
            patch.object(recon_mod, "load_env", return_value={"MUSIC_ROOT": str(music_root)}),
            patch.object(recon_mod, "load_schema", return_value=sample_schema),
        ):
            recon_mod.main()

        return json.loads(delta_path.read_text()), tmp_path

    def test_reconciles_exact_filename_match(self, tmp_path, sample_schema, sample_delta):
        # "Artist B - Missing Track.mp3" in unsorted folder matches delta
        delta, root = self._setup(
            tmp_path, sample_schema, sample_delta,
            unsorted_files={"chill set": ["Artist B - Missing Track.mp3"]},
        )
        assert delta["total_missing"] == 2  # reduced from 3
        artists = [t["artist"] for t in delta["tracks"]]
        assert "Artist B" not in artists

    def test_moves_file_to_correct_schema_folder(self, tmp_path, sample_schema, sample_delta):
        delta, root = self._setup(
            tmp_path, sample_schema, sample_delta,
            unsorted_files={"chill set": ["Artist B - Missing Track.mp3"]},
        )
        dest = root / "music" / "afro house" / "Artist B - Missing Track.mp3"
        assert dest.exists()

    def test_removes_track_from_delta_when_found(self, tmp_path, sample_schema, sample_delta):
        delta, root = self._setup(
            tmp_path, sample_schema, sample_delta,
            unsorted_files={"chill set": ["Artist B - Missing Track.mp3"]},
        )
        titles = [t["title"] for t in delta["tracks"]]
        assert "Missing Track" not in titles

    def test_spotidownloader_swapped_filename(self, tmp_path, sample_schema, sample_delta):
        """SpotiDownloader.com - Love Story - Victor Ruiz.mp3 → should match Victor Ruiz - Love Story"""
        delta, root = self._setup(
            tmp_path, sample_schema, sample_delta,
            unsorted_files={
                "Set House Play Around": ["SpotiDownloader.com - Love Story - Victor Ruiz.mp3"]
            },
        )
        artists = [t["artist"] for t in delta["tracks"]]
        assert "Victor Ruiz" not in artists
        dest = root / "music" / "tech house" / "SpotiDownloader.com - Love Story - Victor Ruiz.mp3"
        assert dest.exists()

    def test_vintage_culture_mangled_filename(self, tmp_path, sample_schema, sample_delta):
        """01-vintage_culture_and_gabss_-_lost_(original_mix).mp3 → matches Vintage Culture & Gabss / Lost"""
        # Use the realistic Spotify artist name which includes both artists
        delta = dict(sample_delta)
        delta["tracks"] = [
            t if t["artist"] != "Vintage Culture"
            else {**t, "artist": "Vintage Culture & Gabss"}
            for t in sample_delta["tracks"]
        ]
        delta, root = self._setup(
            tmp_path, sample_schema, delta,
            unsorted_files={
                "chill set": ["01-vintage_culture_and_gabss_-_lost_(original_mix).mp3"]
            },
        )
        artists = [t["artist"] for t in delta["tracks"]]
        assert "Vintage Culture & Gabss" not in artists

    def test_unmatched_files_stay_in_original_folder(self, tmp_path, sample_schema, sample_delta):
        delta, root = self._setup(
            tmp_path, sample_schema, sample_delta,
            unsorted_files={"chill set": ["Completely Unrelated - Song.mp3"]},
        )
        assert (root / "music" / "chill set" / "Completely Unrelated - Song.mp3").exists()

    def test_unmatched_delta_tracks_remain_in_delta(self, tmp_path, sample_schema, sample_delta):
        delta, root = self._setup(
            tmp_path, sample_schema, sample_delta,
            unsorted_files={"chill set": ["Completely Unrelated - Song.mp3"]},
        )
        assert delta["total_missing"] == 3  # unchanged

    def test_delta_updated_in_place(self, tmp_path, sample_schema, sample_delta):
        delta, root = self._setup(
            tmp_path, sample_schema, sample_delta,
            unsorted_files={"chill set": ["Artist B - Missing Track.mp3"]},
        )
        assert "reconciled_from_unsorted" in delta
        assert delta["reconciled_from_unsorted"] == 1
        assert "last_reconciled_at" in delta

    def test_no_unsorted_dirs_exits_cleanly(self, tmp_path, sample_schema, sample_delta):
        """When all local dirs are schema-aligned, reconcile does nothing."""
        delta, root = self._setup(
            tmp_path, sample_schema, sample_delta,
            unsorted_files={},
        )
        assert delta["total_missing"] == 3  # unchanged

    def test_skips_schema_aligned_folders(self, tmp_path, sample_schema, sample_delta):
        """Files in schema folders should NOT be re-matched against delta."""
        # Put a file in the schema "afro house" folder — it should NOT be double-processed
        delta, root = self._setup(
            tmp_path, sample_schema, sample_delta,
            unsorted_files={},
            schema_files={"afro house": ["Artist B - Missing Track.mp3"]},
        )
        # 03b should not touch schema folders — delta stays at 3
        assert delta["total_missing"] == 3

    def test_non_audio_files_ignored(self, tmp_path, sample_schema, sample_delta):
        delta, root = self._setup(
            tmp_path, sample_schema, sample_delta,
            unsorted_files={"chill set": ["artwork.jpg", "notes.txt", "cover.png"]},
        )
        assert delta["total_missing"] == 3  # no audio files found, no change

    def test_exits_if_delta_missing(self, tmp_path, sample_schema):
        with (
            patch.object(recon_mod, "PROJECT_ROOT", tmp_path),
            patch.object(recon_mod, "DELTA_PATH", tmp_path / "nonexistent.json"),
            patch.object(recon_mod, "load_env", return_value={"MUSIC_ROOT": str(tmp_path / "music")}),
            patch.object(recon_mod, "load_schema", return_value=sample_schema),
        ):
            with pytest.raises(SystemExit):
                recon_mod.main()

    def test_multiple_matches_in_same_run(self, tmp_path, sample_schema, sample_delta):
        """All three delta tracks have matching files in unsorted folders."""
        delta_input = dict(sample_delta)
        delta_input["tracks"] = [
            t if t["artist"] != "Vintage Culture"
            else {**t, "artist": "Vintage Culture & Gabss"}
            for t in sample_delta["tracks"]
        ]
        delta, root = self._setup(
            tmp_path, sample_schema, delta_input,
            unsorted_files={
                "chill set": [
                    "Artist B - Missing Track.mp3",
                    "SpotiDownloader.com - Love Story - Victor Ruiz.mp3",
                    "01-vintage_culture_and_gabss_-_lost_(original_mix).mp3",
                ],
            },
        )
        assert delta["total_missing"] == 0
        assert delta["reconciled_from_unsorted"] == 3
