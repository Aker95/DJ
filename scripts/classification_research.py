#!/usr/bin/env python3
"""Prepare the central research document for a Spotify playlist."""

from __future__ import annotations

import argparse
from pathlib import Path

from utils.config import load_config
from utils.io import read_json, write_json
from utils.metadata import collect_evidence
from utils.research import prepare_document, refresh_summary, render_markdown
from utils.schema import load_schema
from utils.spotify import READ_SCOPE, fetch_playlist_tracks, get_client, resolve_playlist


def prepare_playlist_research(playlist_value: str) -> dict:
    config = load_config()
    schema = load_schema(config.schema_path)
    spotify = get_client(config, scope=READ_SCOPE)
    playlist = resolve_playlist(spotify, playlist_value)
    tracks = fetch_playlist_tracks(spotify, playlist["id"], rich=True)
    if tracks is None:
        raise ValueError("The Spotify playlist is not accessible")
    for index, track in enumerate(tracks, 1):
        print(f"Collecting evidence {index}/{len(tracks)}: {track['artist']} — {track['title']}")
        track["provider_evidence"] = collect_evidence(track, config)
    document = prepare_document(playlist, tracks, schema)
    write_json(config.data_dir / "research.json", document)
    (config.data_dir / "research.md").write_text(render_markdown(document), encoding="utf-8")
    return document


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--playlist", help="Spotify playlist name, URI, ID, or URL")
    source.add_argument(
        "--render",
        type=Path,
        metavar="RESEARCH_JSON",
        help="Regenerate data/research.md after editing research JSON",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.render:
        config = load_config()
        document = refresh_summary(read_json(args.render))
        write_json(args.render, document)
        (config.data_dir / "research.md").write_text(
            render_markdown(document), encoding="utf-8"
        )
        print("Regenerated data/research.md from the reviewed JSON.")
        return
    document = prepare_playlist_research(args.playlist)
    print(f"Prepared {document['summary']['tracks']} tracks in data/research.json and data/research.md")


if __name__ == "__main__":
    main()
