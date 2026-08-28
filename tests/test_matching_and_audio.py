from acquire import normalized_bpm_analysis, version_tokens
from tests.helpers import sample_schema
from utils.matching import filename_matches, preprocess_stem


def test_filename_matching_handles_site_prefix_and_mix_suffix():
    assert filename_matches(
        "SpotiDownloader.com - Track Name - Artist Name (Original Mix).flac",
        "Artist Name",
        "Track Name",
    )
    assert preprocess_stem("01 Artist Name - Track Name (Original Mix)") == (
        "Artist Name",
        "Track Name",
    )


def test_bpm_analysis_normalizes_double_time_into_schema():
    result = normalized_bpm_analysis(
        {"raw_bpm": 68.0, "bpm_estimates": [68.2]}, sample_schema()
    )
    assert result["selected_bpm"] == 136.0
    assert result["selected_master_folder"] == "FAST"
    assert result["bpm_ambiguous"] is False


def test_version_tokens_use_word_boundaries():
    assert version_tokens("Track (Live)") == {"live"}
    assert "live" not in version_tokens("Deliver")
