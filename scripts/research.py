#!/usr/bin/env python3
"""Agent-tool adapter for classification research and approval."""

from __future__ import annotations

import argparse
from pathlib import Path

from dj_library.artifacts import create_run_dir, read_json, tool_result, write_json
from dj_library.config import Settings
from dj_library.research import (
    apply_decisions,
    approve_research,
    prepare_research,
    render_markdown,
    validate_research,
)
from dj_library.schema import load_schema, save_schema
from dj_library.spotify import READ_SCOPE, get_client
from dj_library.tooling import run

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--playlist", required=True)
    prepare.add_argument("--run-id")
    validate = commands.add_parser("validate")
    validate.add_argument("--input", required=True, type=Path)
    approve = commands.add_parser("approve")
    approve.add_argument("--input", required=True, type=Path)
    approve.add_argument("--decisions", type=Path)
    approve.add_argument("--run-id")
    return parser.parse_args()


def execute(args: argparse.Namespace) -> dict:
    settings = Settings.from_env(PROJECT_ROOT)
    schema = load_schema(settings.schema_path)
    if args.action == "prepare":
        run_dir = create_run_dir(settings, "research", args.run_id)
        research = prepare_research(
            args.playlist,
            spotify=get_client(settings, scope=READ_SCOPE),
            schema=schema,
        )
        json_path = write_json(run_dir / "provisional.json", research)
        markdown_path = run_dir / "provisional.md"
        markdown_path.write_text(render_markdown(research), encoding="utf-8")
        return tool_result(
            "research.prepare",
            run_id=run_dir.name,
            artifacts={"research": str(json_path), "review": str(markdown_path)},
            summary=research["summary"],
        )
    if args.action == "validate":
        summary = validate_research(read_json(args.input), schema)
        return tool_result(
            "research.validate", artifacts={"input": str(args.input)}, summary=summary
        )

    run_dir = create_run_dir(settings, "research", args.run_id)
    research = read_json(args.input)
    decisions = read_json(args.decisions) if args.decisions else None
    reviewed = apply_decisions(research, decisions) if decisions else research
    results, updated_schema, approval = approve_research(reviewed, schema)
    save_schema(updated_schema, settings.schema_path)
    reviewed_path = write_json(run_dir / "reviewed.json", reviewed)
    (run_dir / "reviewed.md").write_text(render_markdown(reviewed), encoding="utf-8")
    results_path = write_json(run_dir / "approved.json", results)
    approval_path = write_json(run_dir / "approval_manifest.json", approval)
    return tool_result(
        "research.approve",
        run_id=run_dir.name,
        artifacts={
            "reviewed": str(reviewed_path),
            "approved": str(results_path),
            "approval_manifest": str(approval_path),
            "schema": str(settings.schema_path),
        },
        summary=results["summary"],
    )


def main() -> int:
    args = parse_args()
    return run(f"research.{args.action}", lambda: execute(args))


if __name__ == "__main__":
    raise SystemExit(main())
