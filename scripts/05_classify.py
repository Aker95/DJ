#!/usr/bin/env python3
"""
05_classify.py — Standalone subgenre classifier.

Classifies one or more tracks using the consensus metadata classifier and
prints the result with full per-provider evidence.

Usage:
  python scripts/05_classify.py "Black Coffee - Superman"
  python scripts/05_classify.py "Artist - Title" "Artist2 - Title2"
  python scripts/05_classify.py --from-delta
  python scripts/05_classify.py --from-delta --output results.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.classifier import ConsensusClassifier
from utils.config import load_env, load_schema

DELTA_PATH = Path(__file__).parents[1] / "delta.json"


def _parse_track(raw: str) -> tuple[str, str]:
    """Split 'Artist - Title' into (artist, title)."""
    if " - " not in raw:
        raise ValueError(f"Expected 'Artist - Title' format, got: {raw!r}")
    artist, _, title = raw.partition(" - ")
    return artist.strip(), title.strip()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify electronic music tracks into schema subgenres."
    )
    parser.add_argument(
        "tracks",
        nargs="*",
        metavar="ARTIST - TITLE",
        help="One or more tracks in 'Artist - Title' format.",
    )
    parser.add_argument(
        "--from-delta",
        action="store_true",
        help=f"Read tracks from {DELTA_PATH} instead of CLI arguments.",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Write JSON results to this file.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print the raw API response (ev.raw) for each provider.",
    )
    args = parser.parse_args()

    if not args.from_delta and not args.tracks:
        parser.error(
            "Provide at least one 'Artist - Title' argument or use --from-delta."
        )

    env = load_env()
    schema = load_schema()
    classifier = ConsensusClassifier(env=env, schema=schema)

    tracks: list[tuple[str, str, str | None]] = []  # (artist, title, isrc)

    if args.from_delta:
        with open(DELTA_PATH, encoding="utf-8") as f:
            delta = json.load(f)
        for t in delta.get("tracks", []):
            tracks.append((t["artist"], t["title"], None))
    else:
        for raw in args.tracks:
            try:
                artist, title = _parse_track(raw)
                tracks.append((artist, title, None))
            except ValueError as exc:
                print(f"[SKIP] {exc}", file=sys.stderr)

    if not tracks:
        print("No tracks to classify.", file=sys.stderr)
        sys.exit(1)

    results = []
    for artist, title, isrc in tracks:
        print(f"\n{'=' * 60}")
        print(f"Track: {artist} \u2014 {title}")
        print("=" * 60)
        result = classifier.classify(artist, title, isrc)
        print(result.explain())
        if args.raw:
            print("\nRaw provider responses:")
            for ev in result.evidence:
                print(f"  [{ev.provider}] {ev.raw}")
        results.append(
            {
                "artist": artist,
                "title": title,
                "subgenre": result.subgenre,
                "confidence": round(result.confidence, 3),
                "record_label": result.record_label,
                "evidence": [
                    {"provider": ev.provider, "genres": ev.genres}
                    for ev in result.evidence
                ],
            }
        )

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(
            json.dumps(results, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"\nResults written to {output_path}")


if __name__ == "__main__":
    main()
