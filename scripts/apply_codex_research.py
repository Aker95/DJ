#!/usr/bin/env python3
"""Apply Codex research decisions to a prepared Module 06 artifact.

The research decision file contains only judgment/evidence data. This script
performs every deterministic step: identity matching, field normalization,
summary refresh, validation, and Markdown rendering.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from utils.config import PROJECT_ROOT, load_schema


def load_workflow():
    path = PROJECT_ROOT / "scripts/06_playlist_workflow.py"
    spec = importlib.util.spec_from_file_location("workflow06", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def apply_decisions(*, research_path: Path, decisions_path: Path, markdown_path: Path) -> dict:
    workflow = load_workflow()
    schema = load_schema()
    research = json.loads(research_path.read_text(encoding="utf-8"))
    decisions_doc = json.loads(decisions_path.read_text(encoding="utf-8"))
    decisions = decisions_doc.get("tracks", decisions_doc)
    if "groups" in decisions_doc:
        decisions = {}
        for group_index, group in enumerate(decisions_doc["groups"], start=1):
            track_ids = group.get("track_ids", [])
            fields = group.get("decision", {})
            if not track_ids:
                raise ValueError(f"Decision group {group_index} has no track_ids")
            for track_id in track_ids:
                if track_id in decisions:
                    raise ValueError(f"Duplicate grouped decision for {track_id}")
                decisions[track_id] = {**fields, "spotify_track_id": track_id}
        for track_id, override in decisions_doc.get("track_overrides", {}).items():
            if track_id not in decisions:
                raise ValueError(f"Override references ungrouped track {track_id}")
            decisions[track_id].update(override)
    if isinstance(decisions, list):
        decisions = {item["spotify_track_id"]: item for item in decisions}

    source_ids = [track["spotify_track_id"] for track in research.get("tracks", [])]
    missing = sorted(set(source_ids) - set(decisions))
    extra = sorted(set(decisions) - set(source_ids))
    if missing or extra:
        raise ValueError(f"Decision identity mismatch: missing={missing}, extra={extra}")

    allowed = {
        "main_genre", "subgenre", "classification_level", "master_folder",
        "confidence", "rationale", "evidence", "review_flags",
        "is_new_main_genre_proposal", "is_new_subgenre_proposal",
    }
    for track in research["tracks"]:
        decision = decisions[track["spotify_track_id"]]
        unexpected = set(decision) - allowed - {"spotify_track_id"}
        if unexpected:
            raise ValueError(f"Unexpected decision fields for {track['spotify_track_id']}: {unexpected}")
        track.update({key: value for key, value in decision.items() if key in allowed})
        track["classification_level"] = "subgenre" if track.get("subgenre") else "main_genre"
        track["review_status"] = "proposed"
        track.setdefault("is_new_main_genre_proposal", False)
        track.setdefault("is_new_subgenre_proposal", False)

    for field in (
        "proposed_main_genres", "proposed_subgenres", "proposed_aliases",
        "proposed_routing_changes",
    ):
        research[field] = decisions_doc.get(field, [])

    workflow.refresh_research_summary(research, schema)
    workflow.validate_research(research, schema)
    research_path.write_text(
        json.dumps(research, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(workflow.render_research_markdown(research), encoding="utf-8")
    return research


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research", required=True, type=Path)
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    args = parser.parse_args()
    result = apply_decisions(
        research_path=args.research,
        decisions_path=args.decisions,
        markdown_path=args.markdown,
    )
    print(f"Applied and validated {result['total_tracks']} Codex research decisions.")
    print(f"Research JSON: {args.research}")
    print(f"Research Markdown: {args.markdown}")


if __name__ == "__main__":
    main()
