#!/usr/bin/env python3
"""Preview or apply taxonomy changes explicitly accepted during research."""

from __future__ import annotations

import argparse
from pathlib import Path

from utils.config import load_config
from utils.io import now_iso, read_json, write_json
from utils.schema import apply_schema_changes, load_schema, save_schema


def accepted_changes(research: dict, decisions: dict | None) -> list[dict]:
    if decisions and "schema_changes" in decisions:
        return decisions["schema_changes"]
    return research.get("accepted_schema_changes", [])


def update_schema(research_path: Path, decisions_path: Path | None, apply: bool) -> dict:
    config = load_config()
    research = read_json(research_path)
    decisions = read_json(decisions_path) if decisions_path else None
    changes = accepted_changes(research, decisions)
    updated, report = apply_schema_changes(load_schema(config.schema_path), changes)
    plan = {
        "created_at": now_iso(),
        "changes": report,
        "would_change": any(item.get("changed", item.get("created") or item.get("placement_added")) for item in report),
    }
    write_json(config.data_dir / "schema_plan.json", plan)
    if apply:
        save_schema(updated, config.schema_path)
    return plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("research", type=Path)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plan = update_schema(args.research, args.decisions, args.apply)
    verb = "Applied" if args.apply else "Previewed"
    print(f"{verb} {len(plan['changes'])} accepted schema change(s). See data/schema_plan.json")


if __name__ == "__main__":
    main()
