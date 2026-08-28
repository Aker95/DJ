#!/usr/bin/env python3
"""Validate the canonical music taxonomy without changing it."""

import argparse

from utils.config import load_config
from utils.schema import iter_destinations, load_schema


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate music_schema_knowledge.json without changing it."
    )
    parser.parse_args()
    config = load_config()
    schema = load_schema(config.schema_path)
    destinations = list(iter_destinations(schema))
    print(
        f"Schema {schema['_meta']['version']} is valid: "
        f"{len(schema['master_folders'])} BPM masters, "
        f"{len(schema['main_genres'])} main genres, {len(destinations)} destinations."
    )


if __name__ == "__main__":
    main()
