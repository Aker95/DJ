from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest

from dj_library.artifacts import write_json
from tests.helpers import sample_schema

pytestmark = pytest.mark.integration

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load_adapter(name: str):
    spec = importlib.util.spec_from_file_location(f"adapter_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def project(tmp_path):
    write_json(tmp_path / "music_schema_knowledge.json", sample_schema())
    return tmp_path


def assert_contract(payload, action):
    assert set(payload) >= {"ok", "action", "run_id", "artifacts", "summary"}
    assert payload["ok"] is True
    assert payload["action"] == action


def test_all_five_adapters_return_the_json_contract(tmp_path):
    root = project(tmp_path)

    spotify = load_adapter("spotify")
    spotify.PROJECT_ROOT = root
    approved = write_json(root / "approved.json", {"source_playlist_id": "source", "tracks": []})
    assert_contract(
        spotify.execute(
            argparse.Namespace(
                action="sort-plan", input=approved, keep_source=False, run_id="spotify-run"
            )
        ),
        "spotify.sort-plan",
    )

    library = load_adapter("library")
    library.PROJECT_ROOT = root
    assert_contract(
        library.execute(argparse.Namespace(action="structure-plan", run_id="library-run")),
        "library.structure-plan",
    )

    sync = load_adapter("sync")
    sync.PROJECT_ROOT = root
    snapshot = write_json(root / "snapshot.json", {"playlists": []})
    assert_contract(
        sync.execute(argparse.Namespace(action="delta", snapshot=snapshot, run_id="sync-run")),
        "sync.delta",
    )

    research = load_adapter("research")
    research.PROJECT_ROOT = root
    research_input = write_json(root / "research.json", {"tracks": []})
    assert_contract(
        research.execute(argparse.Namespace(action="validate", input=research_input)),
        "research.validate",
    )

    rekordbox = load_adapter("rekordbox")
    rekordbox.PROJECT_ROOT = root
    assert_contract(
        rekordbox.execute(
            argparse.Namespace(action="export", output=root / "export.xml", run_id="rekordbox-run")
        ),
        "rekordbox.export",
    )
