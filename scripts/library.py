#!/usr/bin/env python3
"""Agent-tool adapter for local structure, audits, deduplication, and alignment."""

from __future__ import annotations

import argparse
from pathlib import Path

from dj_library.artifacts import create_run_dir, read_json, tool_result, write_json
from dj_library.config import Settings
from dj_library.deduplication import apply_deduplication_manifest, build_deduplication_manifest
from dj_library.reconciliation import (
    apply_alignment_manifest,
    audit_library,
    build_alignment_manifest,
)
from dj_library.schema import (
    apply_structure_manifest,
    build_structure_manifest,
    load_schema,
)
from dj_library.spotify import WRITE_SCOPE, get_client
from dj_library.sync import load_registry, merge_registry
from dj_library.tooling import run

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    structure_plan = commands.add_parser("structure-plan")
    structure_plan.add_argument("--run-id")
    structure_apply = commands.add_parser("structure-apply")
    structure_apply.add_argument("--manifest", required=True, type=Path)
    audit = commands.add_parser("audit")
    audit.add_argument("--run-id")
    dedupe_plan = commands.add_parser("dedupe-plan")
    dedupe_plan.add_argument("--run-id")
    dedupe_apply = commands.add_parser("dedupe-apply")
    dedupe_apply.add_argument("--manifest", required=True, type=Path)
    alignment_plan = commands.add_parser("alignment-plan")
    alignment_plan.add_argument("--review", required=True, type=Path)
    alignment_plan.add_argument("--decisions", required=True, type=Path)
    alignment_plan.add_argument("--run-id")
    alignment_apply = commands.add_parser("alignment-apply")
    alignment_apply.add_argument("--manifest", required=True, type=Path)
    return parser.parse_args()


def execute(args: argparse.Namespace) -> dict:
    settings = Settings.from_env(PROJECT_ROOT)
    schema = load_schema(settings.schema_path)
    registry = load_registry(settings.registry_path)
    if args.action == "structure-plan":
        run_dir = create_run_dir(settings, "library", args.run_id)
        manifest = build_structure_manifest(schema, settings=settings)
        path = write_json(run_dir / "structure_manifest.json", manifest)
        return tool_result(
            "library.structure-plan",
            run_id=run_dir.name,
            artifacts={"manifest": str(path)},
            summary=manifest["summary"],
        )
    if args.action == "structure-apply":
        result = apply_structure_manifest(read_json(args.manifest), settings=settings)
        path = write_json(args.manifest.parent / "structure_apply_result.json", result)
        return tool_result(
            "library.structure-apply", artifacts={"result": str(path)}, summary=result["summary"]
        )
    if args.action == "audit":
        run_dir = create_run_dir(settings, "library", args.run_id)
        artifact = audit_library(schema, settings=settings, registry=registry)
        path = write_json(run_dir / "audit.json", artifact)
        return tool_result(
            "library.audit",
            run_id=run_dir.name,
            artifacts={"audit": str(path)},
            summary=artifact["summary"],
        )
    if args.action == "dedupe-plan":
        run_dir = create_run_dir(settings, "library", args.run_id)
        quarantine = settings.music_root / "review" / "dedup" / run_dir.name
        manifest = build_deduplication_manifest(settings, quarantine_root=quarantine)
        path = write_json(run_dir / "deduplication_manifest.json", manifest)
        return tool_result(
            "library.dedupe-plan",
            run_id=run_dir.name,
            artifacts={"manifest": str(path)},
            summary=manifest["summary"],
        )
    if args.action == "dedupe-apply":
        result = apply_deduplication_manifest(read_json(args.manifest), settings=settings)
        path = write_json(args.manifest.parent / "deduplication_apply_result.json", result)
        return tool_result(
            "library.dedupe-apply", artifacts={"result": str(path)}, summary=result["summary"]
        )
    if args.action == "alignment-plan":
        run_dir = create_run_dir(settings, "library", args.run_id)
        manifest = build_alignment_manifest(
            read_json(args.review),
            read_json(args.decisions),
            schema,
            settings=settings,
        )
        path = write_json(run_dir / "alignment_manifest.json", manifest)
        return tool_result(
            "library.alignment-plan",
            run_id=run_dir.name,
            artifacts={"manifest": str(path)},
            summary=manifest["summary"],
        )
    manifest = read_json(args.manifest)
    result = apply_alignment_manifest(
        manifest,
        spotify=get_client(settings, scope=WRITE_SCOPE),
        settings=settings,
    )
    merge_registry(registry, result["tracks"], status="final_aligned")
    write_json(settings.registry_path, registry)
    path = write_json(args.manifest.parent / "alignment_apply_result.json", result)
    return tool_result(
        "library.alignment-apply",
        artifacts={"result": str(path), "registry": str(settings.registry_path)},
        summary=result["summary"],
    )


def main() -> int:
    args = parse_args()
    return run(f"library.{args.action}", lambda: execute(args))


if __name__ == "__main__":
    raise SystemExit(main())
