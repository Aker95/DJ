from __future__ import annotations

import json

from dj_library.tooling import run


def test_run_emits_one_success_object(capsys):
    status = run("demo", lambda: {"ok": True, "action": "demo"})
    output = capsys.readouterr()
    assert status == 0
    assert json.loads(output.out) == {"ok": True, "action": "demo"}
    assert output.err == ""


def test_run_emits_stable_failure_and_diagnostic(capsys):
    def fail():
        raise ValueError("invalid")

    status = run("demo", fail)
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert status == 1
    assert payload["ok"] is False
    assert payload["action"] == "demo"
    assert "ValueError: invalid" in output.err
