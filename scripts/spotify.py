#!/usr/bin/env python3
"""Agent-tool adapter for Spotify authentication, sorting, and schema playlists."""

from __future__ import annotations

import argparse
from pathlib import Path

from dj_library.artifacts import create_run_dir, read_json, tool_result, write_json
from dj_library.config import Settings
from dj_library.schema import load_schema
from dj_library.spotify import (
    READ_SCOPE,
    WRITE_SCOPE,
    apply_schema_manifest,
    apply_sort_manifest,
    audit_sort,
    authenticate,
    build_schema_manifest,
    build_sort_manifest,
    get_client,
)
from dj_library.tooling import run

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("auth")
    sort_plan = commands.add_parser("sort-plan")
    sort_plan.add_argument("--input", required=True, type=Path)
    sort_plan.add_argument("--keep-source", action="store_true")
    sort_plan.add_argument("--run-id")
    sort_apply = commands.add_parser("sort-apply")
    sort_apply.add_argument("--manifest", required=True, type=Path)
    sort_audit = commands.add_parser("sort-audit")
    sort_audit.add_argument("--input", required=True, type=Path)
    schema_plan = commands.add_parser("schema-plan")
    schema_plan.add_argument("--run-id")
    schema_apply = commands.add_parser("schema-apply")
    schema_apply.add_argument("--manifest", required=True, type=Path)
    schema_apply.add_argument("--public", action="store_true")
    return parser.parse_args()


def execute(args: argparse.Namespace) -> dict:
    settings = Settings.from_env(PROJECT_ROOT)
    if args.action == "auth":
        return tool_result("spotify.auth", summary=authenticate(settings))
    if args.action == "sort-plan":
        run_dir = create_run_dir(settings, "spotify", args.run_id)
        manifest = build_sort_manifest(
            read_json(args.input), remove_from_source=not args.keep_source
        )
        path = write_json(run_dir / "sort_manifest.json", manifest)
        return tool_result(
            "spotify.sort-plan",
            run_id=run_dir.name,
            artifacts={"manifest": str(path)},
            summary=manifest["summary"],
        )
    if args.action == "sort-apply":
        result = apply_sort_manifest(
            read_json(args.manifest), spotify=get_client(settings, scope=WRITE_SCOPE)
        )
        path = write_json(args.manifest.parent / "sort_apply_result.json", result)
        return tool_result(
            "spotify.sort-apply", artifacts={"result": str(path)}, summary=result["summary"]
        )
    if args.action == "sort-audit":
        result = audit_sort(read_json(args.input), spotify=get_client(settings, scope=READ_SCOPE))
        return tool_result(
            "spotify.sort-audit", artifacts={"input": str(args.input)}, summary=result
        )
    schema = load_schema(settings.schema_path)
    if args.action == "schema-plan":
        run_dir = create_run_dir(settings, "spotify", args.run_id)
        manifest = build_schema_manifest(
            schema,
            spotify=get_client(settings, scope=READ_SCOPE),
            settings=settings,
        )
        path = write_json(run_dir / "schema_manifest.json", manifest)
        return tool_result(
            "spotify.schema-plan",
            run_id=run_dir.name,
            artifacts={"manifest": str(path)},
            summary=manifest["summary"],
        )
    result = apply_schema_manifest(
        read_json(args.manifest),
        spotify=get_client(settings, scope=WRITE_SCOPE),
        schema=schema,
        settings=settings,
        public=args.public,
    )
    path = write_json(args.manifest.parent / "schema_apply_result.json", result)
    return tool_result(
        "spotify.schema-apply",
        artifacts={"result": str(path), "schema": str(settings.schema_path)},
        summary=result["summary"],
    )


def main() -> int:
    args = parse_args()
    return run(f"spotify.{args.action}", lambda: execute(args))


if __name__ == "__main__":
    raise SystemExit(main())
