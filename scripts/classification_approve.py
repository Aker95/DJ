#!/usr/bin/env python3
"""Validate reviewed playlist research and create approved classifications."""

from __future__ import annotations

import argparse
from pathlib import Path

from utils.config import load_config
from utils.io import read_json, write_json
from utils.research import approve_document, render_markdown
from utils.schema import load_schema


def approve(research_path: Path, decisions_path: Path | None = None) -> dict:
    config = load_config()
    research = read_json(research_path)
    decisions = read_json(decisions_path) if decisions_path else None
    classifications, reviewed = approve_document(
        research, decisions, load_schema(config.schema_path)
    )
    write_json(config.data_dir / "classifications.json", classifications)
    write_json(config.data_dir / "research.json", reviewed)
    (config.data_dir / "research.md").write_text(render_markdown(reviewed), encoding="utf-8")
    return classifications


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("research", type=Path)
    parser.add_argument("--decisions", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = approve(args.research, args.decisions)
    print(f"Approved {result['summary']['approved']} tracks in data/classifications.json")


if __name__ == "__main__":
    main()
