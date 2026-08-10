"""
tests/test_05_classify.py

Unit tests for scripts/05_classify.py.
All classifier calls are mocked — no network required.
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts/ is on sys.path so the module can be imported directly.
SCRIPTS_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import importlib
import types

# We import the module under test so we can call main() and _parse_track() directly.
import scripts.utils  # ensure utils package is importable via sys.path manipulation


def _import_classify():
    """Import 05_classify as a module (numeric prefix requires importlib)."""
    spec = importlib.util.spec_from_file_location(
        "classify_05", SCRIPTS_DIR / "05_classify.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


classify_mod = _import_classify()
# Register so patch("classify_05.*") can resolve the module.
sys.modules.setdefault("classify_05", classify_mod)
_parse_track = classify_mod._parse_track
main = classify_mod.main


# ---------------------------------------------------------------------------
# _parse_track
# ---------------------------------------------------------------------------


class TestParseTrack:
    def test_splits_on_dash(self):
        assert _parse_track("Amelie Lens - Falling For You") == (
            "Amelie Lens",
            "Falling For You",
        )

    def test_strips_whitespace(self):
        artist, title = _parse_track("  Black Coffee  -  Superman  ")
        assert artist == "Black Coffee"
        assert title == "Superman"

    def test_uses_first_dash_only(self):
        """Titles containing ' - ' keep the rest intact."""
        artist, title = _parse_track("Artist - Title - Extra")
        assert artist == "Artist"
        assert title == "Title - Extra"

    def test_raises_when_no_separator(self):
        with pytest.raises(ValueError, match="Expected 'Artist - Title' format"):
            _parse_track("NoSeparatorHere")

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError):
            _parse_track("")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    subgenre="Deep House",
    confidence=0.85,
    record_label="Drumcode",
    is_broad_fallback=False,
):
    """Build a minimal ClassificationResult-like mock."""
    ev = MagicMock()
    ev.provider = "Discogs"
    ev.genres = ["deep house"]
    ev.raw = {"styles": ["deep house"]}

    result = MagicMock()
    result.subgenre = subgenre
    result.confidence = confidence
    result.record_label = record_label
    result.is_broad_fallback = is_broad_fallback
    result.evidence = [ev]
    result.explain.return_value = (
        f"Classification: {subgenre!r} (confidence: {int(confidence * 100)}%)\n"
        f"Label: {record_label}"
    )
    return result


# ---------------------------------------------------------------------------
# main() — CLI behaviour
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the main() entry point using monkeypatched sys.argv."""

    def _run(self, argv, capsys, result=None, *, delta_content=None):
        """
        Run main() with given argv, capturing stdout/stderr.
        Returns (stdout, stderr, result_mock).
        """
        if result is None:
            result = _make_result()

        with patch.object(sys, "argv", ["05_classify.py"] + argv), \
             patch("classify_05.load_env", return_value={}), \
             patch("classify_05.load_schema", return_value={}), \
             patch("classify_05.ConsensusClassifier") as MockCC:
            instance = MockCC.return_value
            instance.classify.return_value = result

            if delta_content is not None:
                import builtins
                real_open = builtins.open

                def fake_open(path, *a, **kw):
                    if "delta" in str(path).lower():
                        return StringIO(json.dumps(delta_content))
                    return real_open(path, *a, **kw)

                with patch("builtins.open", side_effect=fake_open):
                    main()
            else:
                main()

        captured = capsys.readouterr()
        return captured.out, captured.err, instance

    # ------------------------------------------------------------------
    # basic single track
    # ------------------------------------------------------------------

    def test_single_track_prints_header_and_result(self, capsys):
        out, _, _ = self._run(["Amelie Lens - Falling For You"], capsys)
        assert "Amelie Lens" in out
        assert "Falling For You" in out
        assert "Deep House" in out

    def test_multiple_tracks_classified(self, capsys):
        out, _, cc = self._run(
            ["Amelie Lens - Falling For You", "Black Coffee - Superman"], capsys
        )
        assert cc.classify.call_count == 2

    def test_classify_called_with_correct_artist_and_title(self, capsys):
        _, _, cc = self._run(["Amelie Lens - Falling For You"], capsys)
        cc.classify.assert_called_once_with("Amelie Lens", "Falling For You", None)

    # ------------------------------------------------------------------
    # --raw flag
    # ------------------------------------------------------------------

    def test_raw_flag_prints_raw_provider_data(self, capsys):
        out, _, _ = self._run(["--raw", "Amelie Lens - Falling For You"], capsys)
        assert "Raw provider responses" in out
        assert "Discogs" in out

    def test_no_raw_flag_omits_raw_section(self, capsys):
        out, _, _ = self._run(["Amelie Lens - Falling For You"], capsys)
        assert "Raw provider responses" not in out

    # ------------------------------------------------------------------
    # bad / missing input
    # ------------------------------------------------------------------

    def test_invalid_format_is_skipped_with_warning(self, capsys):
        # One bad, one good track — bad one should be skipped to stderr, good one classified.
        out, err, cc = self._run(
            ["NOFORMAT", "Amelie Lens - Falling For You"], capsys
        )
        assert "[SKIP]" in err
        assert cc.classify.call_count == 1

    def test_no_tracks_exits_nonzero(self, capsys, monkeypatch):
        with pytest.raises(SystemExit) as exc_info:
            with patch.object(sys, "argv", ["05_classify.py"]), \
                 patch("classify_05.load_env", return_value={}), \
                 patch("classify_05.load_schema", return_value={}), \
                 patch("classify_05.ConsensusClassifier"):
                main()
        assert exc_info.value.code != 0

    def test_all_invalid_tracks_exits_nonzero(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            self._run(["NOFORMAT"], capsys)
        assert exc_info.value.code != 0

    # ------------------------------------------------------------------
    # --from-delta
    # ------------------------------------------------------------------

    def test_from_delta_reads_tracks(self, capsys):
        delta = {
            "tracks": [
                {"artist": "Amelie Lens", "title": "Falling For You"},
                {"artist": "Black Coffee", "title": "Superman"},
            ]
        }
        out, _, cc = self._run(["--from-delta"], capsys, delta_content=delta)
        assert cc.classify.call_count == 2

    def test_from_delta_empty_tracks_exits_nonzero(self, capsys):
        delta = {"tracks": []}
        with pytest.raises(SystemExit) as exc_info:
            self._run(["--from-delta"], capsys, delta_content=delta)
        assert exc_info.value.code != 0

    # ------------------------------------------------------------------
    # --output flag
    # ------------------------------------------------------------------

    def test_output_flag_writes_json_file(self, capsys, tmp_path):
        out_file = tmp_path / "results.json"
        with patch.object(sys, "argv", ["05_classify.py", "--output", str(out_file), "Amelie Lens - Falling For You"]), \
             patch("classify_05.load_env", return_value={}), \
             patch("classify_05.load_schema", return_value={}), \
             patch("classify_05.ConsensusClassifier") as MockCC:
            MockCC.return_value.classify.return_value = _make_result()
            main()

        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["artist"] == "Amelie Lens"
        assert data[0]["title"] == "Falling For You"
        assert data[0]["subgenre"] == "Deep House"
        assert "confidence" in data[0]
        assert "record_label" in data[0]

    def test_output_json_contains_evidence(self, capsys, tmp_path):
        out_file = tmp_path / "results.json"
        with patch.object(sys, "argv", ["05_classify.py", "--output", str(out_file), "Amelie Lens - Falling For You"]), \
             patch("classify_05.load_env", return_value={}), \
             patch("classify_05.load_schema", return_value={}), \
             patch("classify_05.ConsensusClassifier") as MockCC:
            MockCC.return_value.classify.return_value = _make_result()
            main()

        data = json.loads(out_file.read_text())
        ev = data[0]["evidence"]
        assert isinstance(ev, list)
        assert ev[0]["provider"] == "Discogs"
        assert "genres" in ev[0]

    # ------------------------------------------------------------------
    # result shape
    # ------------------------------------------------------------------

    def test_confidence_is_rounded_to_3dp(self, capsys, tmp_path):
        out_file = tmp_path / "results.json"
        result = _make_result(confidence=0.123456789)
        with patch.object(sys, "argv", ["05_classify.py", "--output", str(out_file), "A - B"]), \
             patch("classify_05.load_env", return_value={}), \
             patch("classify_05.load_schema", return_value={}), \
             patch("classify_05.ConsensusClassifier") as MockCC:
            MockCC.return_value.classify.return_value = result
            main()

        data = json.loads(out_file.read_text())
        assert data[0]["confidence"] == round(0.123456789, 3)
