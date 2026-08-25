#!/usr/bin/env python3
"""Agent-tool adapter for snapshot, delta, acquisition, and file verification."""

from __future__ import annotations

import argparse
from pathlib import Path

from dj_library.acquisition import apply_acquisition_plan, build_acquisition_plan
from dj_library.artifacts import create_run_dir, new_run_id, read_json, tool_result, write_json
from dj_library.audio import analyze_audio
from dj_library.config import Settings
from dj_library.schema import load_schema
from dj_library.spotify import READ_SCOPE, build_snapshot, get_client
from dj_library.sync import (
    apply_promotion,
    apply_unsorted_reconciliation,
    build_delta,
    build_unsorted_reconciliation,
    load_registry,
    merge_registry,
    pending_acquisition_ids,
    record_verification,
    verify_acquisition,
)
from dj_library.tooling import run

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--run-id")
    delta = commands.add_parser("delta")
    delta.add_argument("--snapshot", required=True, type=Path)
    delta.add_argument("--run-id")
    reconcile_plan = commands.add_parser("reconcile-plan")
    reconcile_plan.add_argument("--delta", required=True, type=Path)
    reconcile_plan.add_argument("--run-id")
    reconcile_apply = commands.add_parser("reconcile-apply")
    reconcile_apply.add_argument("--manifest", required=True, type=Path)
    reconcile_apply.add_argument("--delta-output", type=Path)
    acquire_plan = commands.add_parser("acquire-plan")
    acquire_plan.add_argument("--delta", required=True, type=Path)
    acquire_plan.add_argument("--run-id")
    acquire_apply = commands.add_parser("acquire-apply")
    acquire_apply.add_argument("--manifest", required=True, type=Path)
    analyze = commands.add_parser("analyze")
    analyze.add_argument("files", nargs="+", type=Path)
    analyze.add_argument("--run-id")
    verify = commands.add_parser("verify")
    verify.add_argument("--manifest", required=True, type=Path)
    promote = commands.add_parser("promote-apply")
    promote.add_argument("--manifest", required=True, type=Path)
    return parser.parse_args()


def execute(args: argparse.Namespace) -> dict:
    settings = Settings.from_env(PROJECT_ROOT)
    schema = load_schema(settings.schema_path)
    registry = load_registry(settings.registry_path)
    if args.action == "snapshot":
        run_dir = create_run_dir(settings, "sync", args.run_id)
        artifact = build_snapshot(get_client(settings, scope=READ_SCOPE), schema, registry=registry)
        path = write_json(run_dir / "snapshot.json", artifact)
        return tool_result(
            "sync.snapshot",
            run_id=run_dir.name,
            artifacts={"snapshot": str(path)},
            summary=artifact["summary"],
        )
    if args.action == "delta":
        run_dir = create_run_dir(settings, "sync", args.run_id)
        artifact = build_delta(
            read_json(args.snapshot),
            schema,
            settings=settings,
            pending_ids=pending_acquisition_ids(settings, registry),
        )
        path = write_json(run_dir / "delta.json", artifact)
        return tool_result(
            "sync.delta",
            run_id=run_dir.name,
            artifacts={"delta": str(path)},
            summary=artifact["summary"],
        )
    if args.action == "reconcile-plan":
        run_dir = create_run_dir(settings, "sync", args.run_id)
        manifest, remaining = build_unsorted_reconciliation(
            read_json(args.delta), schema, settings=settings
        )
        manifest_path = write_json(run_dir / "reconciliation_manifest.json", manifest)
        delta_path = write_json(run_dir / "remaining_delta.json", remaining)
        return tool_result(
            "sync.reconcile-plan",
            run_id=run_dir.name,
            artifacts={"manifest": str(manifest_path), "remaining_delta": str(delta_path)},
            summary=manifest["summary"],
        )
    if args.action == "reconcile-apply":
        manifest = read_json(args.manifest)
        delta_output = args.delta_output or args.manifest.parent / "delta_after_reconcile.json"
        result = apply_unsorted_reconciliation(
            manifest, settings=settings, delta_output=delta_output
        )
        path = write_json(args.manifest.parent / "reconciliation_apply_result.json", result)
        return tool_result(
            "sync.reconcile-apply",
            artifacts={"result": str(path), "delta": str(delta_output)},
            summary=result["summary"],
        )
    if args.action == "acquire-plan":
        run_id = args.run_id or new_run_id()
        run_dir = create_run_dir(settings, "acquisition", run_id)
        plan = build_acquisition_plan(read_json(args.delta), settings=settings, run_id=run_id)
        path = write_json(run_dir / "acquisition_plan.json", plan)
        return tool_result(
            "sync.acquire-plan",
            run_id=run_id,
            artifacts={"manifest": str(path)},
            summary=plan["summary"],
        )
    if args.action == "acquire-apply":
        acquisition = apply_acquisition_plan(read_json(args.manifest), settings=settings)
        path = write_json(args.manifest.parent / "acquisition_manifest.json", acquisition)
        merge_registry(registry, acquisition["tracks"], status="downloaded_pending_analysis")
        write_json(settings.registry_path, registry)
        return tool_result(
            "sync.acquire-apply",
            run_id=acquisition["run_id"],
            artifacts={"manifest": str(path), "registry": str(settings.registry_path)},
            summary=acquisition["summary"],
        )
    if args.action == "analyze":
        run_dir = create_run_dir(settings, "sync", args.run_id)
        analyses = [analyze_audio(path, schema) for path in args.files]
        path = write_json(
            run_dir / "audio_analysis.json",
            {"tracks": analyses, "summary": {"files": len(analyses)}},
        )
        return tool_result(
            "sync.analyze",
            run_id=run_dir.name,
            artifacts={"analysis": str(path)},
            summary={"files": len(analyses)},
        )
    if args.action == "verify":
        acquisition = read_json(args.manifest)
        complete, promotion, review, cache = verify_acquisition(
            acquisition, schema, settings=settings
        )
        paths = {
            "verification": str(
                write_json(args.manifest.parent / "verification_results.json", complete)
            ),
            "promotion_manifest": str(
                write_json(args.manifest.parent / "promotion_manifest.json", promotion)
            ),
            "review_queue": str(
                write_json(args.manifest.parent / "file_review_queue.json", review)
            ),
            "analysis_cache": str(write_json(args.manifest.parent / "analysis_cache.json", cache)),
        }
        record_verification(registry, complete)
        write_json(settings.registry_path, registry)
        return tool_result(
            "sync.verify",
            run_id=acquisition.get("run_id"),
            artifacts=paths,
            summary={
                "verified": len(complete["tracks"]),
                "consistent": promotion["summary"]["consistent"],
                "review": review["summary"]["review"],
            },
        )
    result, registry = apply_promotion(
        read_json(args.manifest), settings=settings, registry=registry
    )
    write_json(settings.registry_path, registry)
    path = write_json(args.manifest.parent / "promotion_apply_result.json", result)
    return tool_result(
        "sync.promote-apply",
        artifacts={"result": str(path), "registry": str(settings.registry_path)},
        summary=result["summary"],
    )


def main() -> int:
    args = parse_args()
    return run(f"sync.{args.action}", lambda: execute(args))


if __name__ == "__main__":
    raise SystemExit(main())
