#!/usr/bin/env python3
"""Find Spotify tracks missing from their canonical local destinations."""

from __future__ import annotations

import argparse
from pathlib import Path

from utils.audio import AUDIO_EXTENSIONS
from utils.config import load_config
from utils.io import now_iso, read_json, write_json
from utils.matching import filename_matches


def folder_contains_track(folder: Path, artist: str, title: str) -> bool:
    if not folder.is_dir():
        return False
    return any(
        path.is_file()
        and path.suffix.casefold() in AUDIO_EXTENSIONS
        and filename_matches(path.name, artist, title)
        for path in folder.iterdir()
    )


def build_delta(snapshot: dict, project_root: Path) -> dict:
    missing: list[dict] = []
    checked = 0
    for playlist in snapshot.get("playlists", []):
        folder = (project_root / playlist["local_folder"]).resolve()
        for track in playlist.get("tracks", []):
            checked += 1
            if folder_contains_track(folder, track.get("artist", ""), track.get("title", "")):
                continue
            missing.append(
                {
                    **track,
                    "master_folder": playlist["master_folder"],
                    "main_genre": playlist["main_genre"],
                    "subgenre": playlist.get("subgenre"),
                    "placement_id": playlist["placement_id"],
                    "source_playlist_id": playlist["spotify_playlist_id"],
                    "local_folder": playlist["local_folder"],
                }
            )
    return {
        "generated_at": now_iso(),
        "tracks": missing,
        "summary": {"checked": checked, "missing": len(missing)},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, nargs="?", default=Path("data/spotify_snapshot.json"))
    return parser.parse_args()


def main() -> None:
    config = load_config()
    delta = build_delta(read_json(parse_args().snapshot), config.project_root)
    write_json(config.data_dir / "delta.json", delta)
    print(f"Checked {delta['summary']['checked']} tracks; {delta['summary']['missing']} are missing.")


if __name__ == "__main__":
    main()
