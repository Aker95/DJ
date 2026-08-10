#!/usr/bin/env python3
"""Generate a Rekordbox XML bridge library from the canonical DJ schema."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

try:
    from mutagen import File as MutagenFile
except ImportError:  # Metadata is optional; paths and playlists still work.
    MutagenFile = None


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = PROJECT_ROOT / "music_schema_knowledge.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "rekordbox_schema.xml"
AUDIO_EXTENSIONS = {
    ".aac",
    ".aif",
    ".aiff",
    ".alac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
}
KIND_BY_EXTENSION = {
    ".aac": "AAC File",
    ".aif": "AIFF File",
    ".aiff": "AIFF File",
    ".alac": "ALAC File",
    ".flac": "FLAC File",
    ".m4a": "M4A File",
    ".mp3": "MP3 File",
    ".ogg": "OGG File",
    ".opus": "Opus File",
    ".wav": "WAV File",
}


def audio_files_directly_in(folder: Path) -> list[Path]:
    """Return audio files directly in a destination, never from child routes."""
    if not folder.is_dir():
        return []
    return sorted(
        (
            path.resolve()
            for path in folder.iterdir()
            if path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS
        ),
        key=lambda path: str(path).casefold(),
    )


def first_tag(tags: object, *keys: str) -> str | None:
    if not tags:
        return None
    for key in keys:
        try:
            value = tags.get(key)  # type: ignore[union-attr]
        except (AttributeError, TypeError):
            continue
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    return None


def read_metadata(path: Path) -> dict[str, str]:
    metadata = {
        "Name": path.stem,
        "Kind": KIND_BY_EXTENSION.get(path.suffix.casefold(), "Audio File"),
        "Size": str(path.stat().st_size),
        "DateModified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
    }
    if MutagenFile is None:
        return metadata

    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        return metadata
    if audio is None:
        return metadata

    title = first_tag(audio.tags, "title")
    artist = first_tag(audio.tags, "artist", "albumartist")
    album = first_tag(audio.tags, "album")
    if title:
        metadata["Name"] = title
    if artist:
        metadata["Artist"] = artist
    if album:
        metadata["Album"] = album

    info = getattr(audio, "info", None)
    length = getattr(info, "length", None)
    bitrate = getattr(info, "bitrate", None)
    sample_rate = getattr(info, "sample_rate", None)
    if length:
        metadata["TotalTime"] = str(round(float(length)))
    if bitrate:
        metadata["BitRate"] = str(round(float(bitrate) / 1000))
    if sample_rate:
        metadata["SampleRate"] = str(round(float(sample_rate)))
    return metadata


def rekordbox_uri(path: Path) -> str:
    """Encode an absolute path using Rekordbox's documented localhost URI form."""
    return "file://localhost" + quote(path.as_posix(), safe="/:~!$&'()*+,;=@")


def playlist_node(name: str, track_ids: list[int]) -> ET.Element:
    node = ET.Element(
        "NODE",
        {
            "Type": "1",
            "Name": name,
            "Entries": str(len(track_ids)),
            "KeyType": "0",
        },
    )
    for track_id in track_ids:
        ET.SubElement(node, "TRACK", {"Key": str(track_id)})
    return node


def generate(schema_path: Path, output_path: Path) -> dict[str, int]:
    with schema_path.open(encoding="utf-8") as handle:
        schema = json.load(handle)

    genres_by_id = {
        genre["id"]: genre for genre in schema.get("main_genres", [])
    }
    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    ET.SubElement(
        root,
        "PRODUCT",
        {"Name": "DJ Schema Generator", "Version": "1.0", "Company": "Personal"},
    )
    collection = ET.SubElement(root, "COLLECTION", {"Entries": "0"})
    playlists = ET.SubElement(root, "PLAYLISTS")
    masters = schema.get("master_folders", [])
    playlist_root = ET.SubElement(
        playlists,
        "NODE",
        {"Type": "0", "Name": "ROOT", "Count": str(len(masters))},
    )

    track_ids: dict[Path, int] = {}
    missing_folders = 0
    playlist_count = 0

    def register_tracks(folder_value: str | None) -> list[int]:
        nonlocal missing_folders
        if not folder_value:
            return []
        folder = (PROJECT_ROOT / folder_value).resolve()
        if not folder.is_dir():
            missing_folders += 1
            return []

        ids: list[int] = []
        for path in audio_files_directly_in(folder):
            track_id = track_ids.get(path)
            if track_id is None:
                track_id = len(track_ids) + 1
                track_ids[path] = track_id
                attributes = {"TrackID": str(track_id), **read_metadata(path)}
                attributes["Location"] = rekordbox_uri(path)
                ET.SubElement(collection, "TRACK", attributes)
            ids.append(track_id)
        return ids

    for master in masters:
        placements = master.get("genre_placements", [])
        master_node = ET.SubElement(
            playlist_root,
            "NODE",
            {"Type": "0", "Name": master["name"], "Count": str(len(placements))},
        )
        for placement in placements:
            genre = genres_by_id.get(placement.get("main_genre_id"))
            if not genre:
                raise ValueError(
                    f"Unknown main_genre_id: {placement.get('main_genre_id')!r}"
                )

            subgenres = placement.get("subgenres", [])
            parent_track_ids = register_tracks(placement.get("local_folder"))
            if not subgenres:
                master_node.append(playlist_node(genre["name"], parent_track_ids))
                playlist_count += 1
                continue

            genre_node = ET.SubElement(
                master_node,
                "NODE",
                {
                    "Type": "0",
                    "Name": genre["name"],
                    "Count": str(1 + len(subgenres)),
                },
            )
            # This same-name child is the broad main-genre fallback playlist.
            genre_node.append(playlist_node(genre["name"], parent_track_ids))
            playlist_count += 1
            for subgenre in subgenres:
                genre_node.append(
                    playlist_node(
                        subgenre["name"],
                        register_tracks(subgenre.get("local_folder")),
                    )
                )
                playlist_count += 1

    collection.set("Entries", str(len(track_ids)))
    ET.indent(root, space="  ")
    xml_body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    output_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_body + "\n",
        encoding="utf-8",
    )
    return {
        "tracks": len(track_ids),
        "master_folders": len(masters),
        "playlists": playlist_count,
        "missing_folders": missing_folders,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = generate(args.schema.resolve(), args.output.resolve())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(
        f"Created {args.output.resolve()} with {summary['tracks']} tracks, "
        f"{summary['master_folders']} master folders, and "
        f"{summary['playlists']} playlists."
    )
    if summary["missing_folders"]:
        print(f"Warning: {summary['missing_folders']} schema folders were missing.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
