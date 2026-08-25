"""Traversal, validation, and routing for the canonical v3 taxonomy."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .artifacts import now_iso, seal_manifest, sha256_file, verify_manifest, write_json
from .config import Settings


def canonical_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    characters: list[str] = []
    for character in decomposed:
        category = unicodedata.category(character)[0]
        if category == "M":
            continue
        characters.append(character if character.isspace() or category in {"L", "N"} else " ")
    separated = "".join(characters)
    normalized = separated.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", normalized.casefold()).strip()


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", canonical_key(value)).strip("-")


def load_schema(path: Path) -> dict:
    schema = json.loads(path.read_text(encoding="utf-8"))
    validate_schema(schema)
    return schema


def save_schema(schema: dict, path: Path) -> None:
    validate_schema(schema)
    write_json(path, schema)


def master_folder_index(schema: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for master in schema.get("master_folders", []):
        for label in [master.get("name", ""), *master.get("aliases", [])]:
            if label:
                result[canonical_key(label)] = master
    return result


def bpm_in_master(master: dict, bpm: float | int) -> bool:
    value = float(bpm)
    minimum = float(master["bpm_min"])
    maximum = float(master["bpm_max"])
    return (
        minimum <= value <= maximum
        if master.get("bpm_max_inclusive")
        else minimum <= value < maximum
    )


def bpm_master(schema: dict, bpm: float | int | None) -> dict | None:
    if bpm is None:
        return None
    return next(
        (master for master in schema.get("master_folders", []) if bpm_in_master(master, bpm)),
        None,
    )


def placement_id(master: dict, main_genre_id: str, subgenre_id: str | None) -> str:
    parts = [master["id"], main_genre_id]
    if subgenre_id:
        parts.append(subgenre_id)
    return ":".join(parts)


def main_genre_index(schema: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for genre in schema.get("main_genres", []):
        for label in [genre.get("name", ""), *genre.get("aliases", [])]:
            if label:
                result[canonical_key(label)] = genre
    return result


def iter_genre_placements(schema: dict) -> Iterator[dict[str, Any]]:
    genres = {genre["id"]: genre for genre in schema.get("main_genres", [])}
    for master in schema.get("master_folders", []):
        for placement in master.get("genre_placements", []):
            genre = genres.get(placement.get("main_genre_id"))
            if genre:
                yield {"master_folder": master, "placement": placement, "main_genre": genre}


def iter_subgenres(schema: dict) -> Iterator[dict[str, Any]]:
    for context in iter_genre_placements(schema):
        for subgenre in context["placement"].get("subgenres", []):
            yield {**context, "subgenre": subgenre}


def explicit_playlist_name(master: dict, genre: dict, subgenre: dict | None) -> str:
    labels = [master.get("spotify_name_prefix") or master["name"], genre["name"]]
    if subgenre:
        labels.append(subgenre["name"])
    return " • ".join(labels)


def iter_destinations(
    schema: dict, *, project_root: Path | None = None
) -> Iterator[dict[str, Any]]:
    """Yield every main-genre fallback and leaf placement."""

    for context in iter_genre_placements(schema):
        master = context["master_folder"]
        placement = context["placement"]
        genre = context["main_genre"]
        local_value = placement.get("local_folder")
        yield {
            "placement_id": placement_id(master, genre["id"], None),
            "classification_level": "main_genre",
            "name": genre["name"],
            "aliases": genre.get("aliases", []),
            "master_folder": master["name"],
            "main_genre": genre["name"],
            "subgenre": None,
            "spotify_playlist": placement.get("spotify_playlist"),
            "spotify_playlist_name": placement.get("spotify_playlist_name")
            or explicit_playlist_name(master, genre, None),
            "bpm_min": master["bpm_min"],
            "bpm_max": master["bpm_max"],
            "bpm_max_inclusive": bool(master.get("bpm_max_inclusive")),
            "local_folder": (
                project_root / local_value if project_root and local_value else local_value
            ),
            "node": placement,
        }
        for subgenre in placement.get("subgenres", []):
            local_value = subgenre.get("local_folder")
            yield {
                "placement_id": placement_id(master, genre["id"], subgenre["id"]),
                "classification_level": "subgenre",
                "name": subgenre["name"],
                "aliases": subgenre.get("aliases", []),
                "master_folder": master["name"],
                "main_genre": genre["name"],
                "subgenre": subgenre["name"],
                "spotify_playlist": subgenre.get("spotify_playlist"),
                "spotify_playlist_name": subgenre.get("spotify_playlist_name")
                or explicit_playlist_name(master, genre, subgenre),
                "bpm_min": master["bpm_min"],
                "bpm_max": master["bpm_max"],
                "bpm_max_inclusive": bool(master.get("bpm_max_inclusive")),
                "local_folder": (
                    project_root / local_value if project_root and local_value else local_value
                ),
                "node": subgenre,
            }


def destination_index(schema: dict) -> dict[tuple[str, str, str], dict[str, Any]]:
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for destination in iter_destinations(schema):
        for label in [destination["name"], *destination.get("aliases", [])]:
            result[
                (
                    canonical_key(destination["master_folder"]),
                    destination["classification_level"],
                    canonical_key(label),
                )
            ] = destination
    return result


def resolve_destination(
    schema: dict,
    *,
    master_folder: str,
    main_genre: str,
    subgenre: str | None,
) -> dict[str, Any] | None:
    master = master_folder_index(schema).get(canonical_key(master_folder))
    if not master:
        return None
    level = "subgenre" if subgenre else "main_genre"
    return destination_index(schema).get(
        (canonical_key(master["name"]), level, canonical_key(subgenre or main_genre))
    )


def validate_schema(schema: dict) -> None:
    """Validate the current hierarchy and global canonical-name uniqueness."""

    version = str(schema.get("_meta", {}).get("version", ""))
    if not version.startswith("3."):
        raise ValueError(f"Unsupported schema version {version!r}; expected v3")

    errors: list[str] = []
    ids: set[str] = set()
    labels: dict[str, str] = {}

    def register(node: dict, kind: str, *, node_id: str | None = None) -> None:
        identifier = node_id or node.get("id")
        name = node.get("name")
        if not identifier or not name:
            errors.append(f"Every {kind} requires id and name")
            return
        if identifier in ids:
            errors.append(f"Duplicate taxonomy id {identifier!r}")
        ids.add(identifier)
        for label in [name, *node.get("aliases", [])]:
            key = canonical_key(label)
            owner = labels.get(key)
            if owner and owner != identifier:
                errors.append(f"Taxonomy label {label!r} duplicates {owner!r}")
            labels[key] = identifier

    genre_ids: set[str] = set()
    for genre in schema.get("main_genres", []):
        register(genre, "main genre")
        genre_ids.add(genre.get("id"))

    masters = sorted(schema.get("master_folders", []), key=lambda item: item.get("bpm_min", 0))
    for index, master in enumerate(masters):
        register(master, "master folder")
        try:
            minimum = float(master["bpm_min"])
            maximum = float(master["bpm_max"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"Master folder {master.get('name')!r} requires BPM bounds")
            continue
        if minimum >= maximum:
            errors.append(f"Master folder {master['name']!r} has an empty BPM interval")
        if index:
            previous = masters[index - 1]
            previous_max = float(previous["bpm_max"])
            if minimum < previous_max or (
                minimum == previous_max and previous.get("bpm_max_inclusive")
            ):
                errors.append("Master-folder BPM intervals overlap")

    placements: set[tuple[str, str]] = set()
    registered_subgenres: dict[str, tuple[str, tuple[str, ...]]] = {}
    for master in schema.get("master_folders", []):
        for placement in master.get("genre_placements", []):
            genre_id = placement.get("main_genre_id")
            key = (master["id"], genre_id)
            if genre_id not in genre_ids:
                errors.append(f"Placement {key!r} references an unknown main genre")
            if key in placements:
                errors.append(f"Duplicate main-genre placement {key!r}")
            placements.add(key)
            if not placement.get("local_folder"):
                errors.append(f"Placement {key!r} requires local_folder")
            for subgenre in placement.get("subgenres", []):
                identifier = subgenre.get("id")
                signature = (subgenre.get("name", ""), tuple(sorted(subgenre.get("aliases", []))))
                previous = registered_subgenres.get(identifier)
                if previous is None:
                    registered_subgenres[identifier] = signature
                    register(subgenre, "subgenre")
                elif previous != signature:
                    errors.append(f"Subgenre {identifier!r} has conflicting canonical labels")
                if not subgenre.get("local_folder"):
                    errors.append(f"Subgenre {subgenre.get('name')!r} requires local_folder")

    if errors:
        raise ValueError("Schema validation failed:\n- " + "\n- ".join(errors))


def build_structure_manifest(schema: dict, *, settings: Settings) -> dict:
    """Plan missing final destination folders without creating them."""

    folders = sorted(
        {
            str(Path(destination["local_folder"]))
            for destination in iter_destinations(schema)
            if destination.get("local_folder")
            and not (settings.project_root / destination["local_folder"]).is_dir()
        }
    )
    return seal_manifest(
        {
            "artifact_type": "library_structure_manifest",
            "artifact_version": 1,
            "status": "ready_for_review",
            "created_at": now_iso(),
            "schema_sha256": sha256_file(settings.schema_path),
            "folders": folders,
            "summary": {"folders_to_create": len(folders)},
        }
    )


def apply_structure_manifest(manifest: dict, *, settings: Settings) -> dict:
    """Create only the exact schema folders recorded in a reviewed manifest."""

    verify_manifest(manifest, artifact_type="library_structure_manifest")
    if sha256_file(settings.schema_path) != manifest["schema_sha256"]:
        raise ValueError("Schema changed after the folder plan was reviewed")
    created = 0
    existing = 0
    for relative in manifest.get("folders", []):
        path = (settings.project_root / relative).resolve()
        try:
            path.relative_to(settings.music_root)
        except ValueError as error:
            raise ValueError(f"Schema folder escapes music root: {relative}") from error
        if path.is_dir():
            existing += 1
        else:
            path.mkdir(parents=True, exist_ok=False)
            created += 1
    return {
        "artifact_type": "library_structure_apply_result",
        "artifact_version": 1,
        "applied_at": now_iso(),
        "manifest_sha256": manifest["manifest_sha256"],
        "summary": {"created": created, "already_existing": existing},
    }
