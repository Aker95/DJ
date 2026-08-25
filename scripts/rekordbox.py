#!/usr/bin/env python3
"""Agent-tool adapter for read-only Rekordbox verification and XML export."""

from __future__ import annotations

import argparse
from pathlib import Path

from dj_library.artifacts import create_run_dir, read_json, tool_result, write_json
from dj_library.config import Settings
from dj_library.rekordbox import export_xml, read_tracks, reconcile, verify_structure
from dj_library.schema import load_schema
from dj_library.sync import load_registry
from dj_library.tooling import run

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = Path.home() / "Library/Pioneer/rekordbox/master.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    verify.add_argument("--run-id")
    reconcile_parser = commands.add_parser("reconcile")
    reconcile_parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    reconcile_parser.add_argument("--reviewed", required=True, type=Path)
    reconcile_parser.add_argument("--run-id")
    export = commands.add_parser("export")
    export.add_argument("--output", type=Path)
    export.add_argument("--run-id")
    return parser.parse_args()


def execute(args: argparse.Namespace) -> dict:
    settings = Settings.from_env(PROJECT_ROOT)
    schema = load_schema(settings.schema_path)
    run_dir = create_run_dir(settings, "rekordbox", args.run_id)
    if args.action == "verify":
        tracks = read_tracks(args.database, schema, settings=settings)
        artifact = verify_structure(tracks, schema, registry=load_registry(settings.registry_path))
        path = write_json(run_dir / "structure_verification.json", artifact)
        return tool_result(
            "rekordbox.verify",
            run_id=run_dir.name,
            artifacts={"verification": str(path)},
            summary=artifact["summary"],
        )
    if args.action == "reconcile":
        tracks = read_tracks(args.database, schema, settings=settings)
        artifact = reconcile(schema, read_json(args.reviewed), tracks)
        path = write_json(run_dir / "reconciliation.json", artifact)
        return tool_result(
            "rekordbox.reconcile",
            run_id=run_dir.name,
            artifacts={"reconciliation": str(path)},
            summary=artifact["summary"],
        )
    output = args.output or run_dir / "rekordbox.xml"
    summary = export_xml(schema, settings=settings, output_path=output)
    return tool_result(
        "rekordbox.export", run_id=run_dir.name, artifacts={"xml": str(output)}, summary=summary
    )


def main() -> int:
    args = parse_args()
    return run(f"rekordbox.{args.action}", lambda: execute(args))


if __name__ == "__main__":
    raise SystemExit(main())
