#!/usr/bin/env python3
"""Generate Rekordbox XML from canonical final music folders."""

from __future__ import annotations

import argparse
from pathlib import Path

from utils.config import load_config
from utils.rekordbox import export_xml
from utils.schema import load_schema


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("rekordbox_schema.xml"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    summary = export_xml(config, load_schema(config.schema_path), args.output)
    print(f"Exported {summary['tracks']} tracks in {summary['playlists']} playlists to {args.output}.")


if __name__ == "__main__":
    main()
