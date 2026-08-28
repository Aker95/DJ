#!/usr/bin/env python3
"""Create read-only feedback from the current Rekordbox library."""

from __future__ import annotations

import argparse
from pathlib import Path

from utils.config import load_config
from utils.io import read_json, write_json
from utils.rekordbox import read_tracks, review_tracks
from utils.schema import load_schema


def parse_args() -> argparse.Namespace:
    config = load_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=config.rekordbox_database)
    parser.add_argument("--classifications", type=Path, default=Path("data/classifications.json"))
    parser.add_argument(
        "--acquisition-results",
        type=Path,
        default=Path("data/acquisition_results.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    schema = load_schema(config.schema_path)
    classifications = read_json(args.classifications) if args.classifications.exists() else None
    acquisitions = (
        read_json(args.acquisition_results) if args.acquisition_results.exists() else None
    )
    review = review_tracks(
        read_tracks(args.database, config, schema), schema, classifications, acquisitions
    )
    write_json(config.data_dir / "rekordbox_review.json", review)
    print(
        f"Reviewed {review['summary']['files']} Rekordbox files; "
        f"{len(review['review_tracks'])} require review."
    )


if __name__ == "__main__":
    main()
