import json
import sys

import acquisition
import local_library
import pytest
import spotify
from artifacts import write_artifact

from tests.helpers import config_for, sample_schema
from tests.test_spotify import FakeSpotify


def _configured_workspace(tmp_path):
    config = config_for(tmp_path)
    config.data_dir.mkdir(parents=True)
    config.schema_path.write_text(json.dumps(sample_schema()), encoding="utf-8")
    return config


def test_local_structure_apply_rejects_stale_catalog(tmp_path, monkeypatch):
    config = _configured_workspace(tmp_path)
    plan = config.data_dir / "structure.json"
    write_artifact(
        plan,
        "library-structure-plan",
        {"inputs": {"catalog": "stale"}, "actions": []},
    )
    monkeypatch.setattr(local_library, "load_config", lambda: config)
    monkeypatch.setattr(
        sys, "argv", ["local_library.py", "structure", "--apply", "--plan", str(plan)]
    )
    with pytest.raises(ValueError, match="stale"):
        local_library.main()


def test_reconciliation_apply_rejects_stale_delta(tmp_path, monkeypatch):
    config = _configured_workspace(tmp_path)
    delta = config.data_dir / "library_delta.json"
    write_artifact(delta, "library-delta", {"tracks": []})
    plan = config.data_dir / "reconcile.json"
    write_artifact(
        plan,
        "library-reconciliation-plan",
        {
            "inputs": {"catalog": "stale", "delta": "stale"},
            "input_paths": {"delta": str(delta)},
            "actions": [],
        },
    )
    monkeypatch.setattr(local_library, "load_config", lambda: config)
    monkeypatch.setattr(
        sys, "argv", ["local_library.py", "reconcile", "--apply", "--plan", str(plan)]
    )
    with pytest.raises(ValueError, match="stale"):
        local_library.main()


def test_acquisition_apply_rejects_stale_delta_before_slskd(tmp_path, monkeypatch):
    config = _configured_workspace(tmp_path)
    delta = config.data_dir / "library_delta.json"
    write_artifact(delta, "library-delta", {"tracks": []})
    plan = config.data_dir / "acquisition.json"
    write_artifact(
        plan,
        "acquisition-plan",
        {
            "inputs": {"catalog": "stale", "delta": "stale"},
            "input_paths": {"delta": str(delta)},
            "actions": [],
        },
    )
    monkeypatch.setattr(acquisition, "load_config", lambda: config)
    monkeypatch.setattr(
        acquisition,
        "_check_slskd",
        lambda _config: (_ for _ in ()).throw(AssertionError("must validate first")),
    )
    monkeypatch.setattr(sys, "argv", ["acquisition.py", "--apply", "--plan", str(plan)])
    with pytest.raises(ValueError, match="stale"):
        acquisition.main()


@pytest.mark.parametrize(
    "command,kind",
    [("provision", "spotify-provision-plan"), ("sort", "spotify-sort-plan")],
)
def test_spotify_apply_commands_reject_stale_inputs(
    tmp_path, monkeypatch, command, kind
):
    config = _configured_workspace(tmp_path)
    input_path = config.data_dir / "classifications.json"
    write_artifact(
        input_path, "classifications", {"source_playlist_id": "source", "tracks": []}
    )
    plan = config.data_dir / f"{command}.json"
    payload = {"inputs": {"catalog": "stale"}, "actions": []}
    if command == "sort":
        payload.update(
            {
                "inputs": {"classifications": "stale"},
                "input_paths": {"classifications": str(input_path)},
                "source_playlist_id": "source",
            }
        )
    write_artifact(plan, kind, payload)
    monkeypatch.setattr(spotify, "load_config", lambda: config)
    monkeypatch.setattr(
        spotify, "authenticate", lambda *_args, **_kwargs: FakeSpotify()
    )
    monkeypatch.setattr(
        sys, "argv", ["spotify.py", command, "--apply", "--plan", str(plan)]
    )
    with pytest.raises(ValueError, match="stale"):
        spotify.main()
