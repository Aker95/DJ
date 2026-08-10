"""Shared schema traversal, canonicalization, and routing helpers.

Schema v2 stores canonical main genres globally and references them from one or
more BPM master-folder placements.  The read helpers intentionally retain v1
support so the numbered workflows and old snapshots can be migrated safely.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Iterator

from utils.config import PROJECT_ROOT


def canonical_key(value: str) -> str:
    """Normalize a taxonomy label for exact canonical/alias comparisons."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def slug(value: str) -> str:
    """Return the stable lowercase folder/id slug used by schema v2."""
    return canonical_key(value).replace(" ", "-") or "genre"


def is_v2(schema: dict) -> bool:
    version = str(schema.get("_meta", {}).get("version", "1"))
    return version.startswith("2") and isinstance(schema.get("main_genres"), list)


def main_genre_index(schema: dict) -> dict[str, dict]:
    """Index every canonical main genre name and alias to its v2 record."""
    result: dict[str, dict] = {}
    for genre in schema.get("main_genres", []):
        for label in [genre.get("name", ""), *genre.get("aliases", [])]:
            if label:
                result[canonical_key(label)] = genre
    return result


def iter_genre_placements(schema: dict) -> Iterator[dict[str, Any]]:
    """Yield normalized placement records for v2 schemas."""
    genres_by_id = {g["id"]: g for g in schema.get("main_genres", [])}
    for master in schema.get("master_folders", []):
        for placement in master.get("genre_placements", []):
            genre = genres_by_id.get(placement.get("main_genre_id"), {})
            yield {
                "master_folder": master,
                "placement": placement,
                "main_genre": genre,
            }


def iter_subgenres(schema: dict) -> Iterator[dict[str, Any]]:
    """Yield every leaf with its master/main context for schema v1 or v2."""
    if is_v2(schema):
        for context in iter_genre_placements(schema):
            for subgenre in context["placement"].get("subgenres", []):
                yield {**context, "subgenre": subgenre}
        return

    for master in schema.get("master_folders", []):
        for subgenre in master.get("subgenres", []):
            yield {
                "master_folder": master,
                "placement": None,
                "main_genre": None,
                "subgenre": subgenre,
            }


def subgenre_index(schema: dict) -> dict[str, dict[str, Any]]:
    """Index canonical leaf names and aliases to normalized context records."""
    result: dict[str, dict[str, Any]] = {}
    for context in iter_subgenres(schema):
        subgenre = context["subgenre"]
        for label in [subgenre.get("name", ""), *subgenre.get("aliases", [])]:
            if label:
                result[canonical_key(label)] = context
    return result


def effective_local_folder(node: dict, *, project_root: Path = PROJECT_ROOT) -> Path | None:
    """Resolve the active path while a reviewed hierarchy migration is pending."""
    legacy = node.get("legacy_local_folder")
    if node.get("migration_status") == "pending" and legacy:
        legacy_path = project_root / legacy
        if legacy_path.exists():
            return legacy_path
    local = node.get("local_folder")
    return project_root / local if local else None


def iter_destinations(
    schema: dict, *, project_root: Path = PROJECT_ROOT
) -> Iterator[dict[str, Any]]:
    """Yield both parent and leaf destinations in schema v2 (leaves only in v1)."""
    if not is_v2(schema):
        for context in iter_subgenres(schema):
            sg = context["subgenre"]
            yield {
                "classification_level": "subgenre",
                "name": sg["name"],
                "aliases": sg.get("aliases", []),
                "master_folder": context["master_folder"]["name"],
                "main_genre": None,
                "subgenre": sg["name"],
                "spotify_playlist": sg.get("spotify_playlist"),
                "local_folder": effective_local_folder(sg, project_root=project_root),
                "node": sg,
                "playlist_node": sg,
            }
        return

    for context in iter_genre_placements(schema):
        master = context["master_folder"]
        placement = context["placement"]
        genre = context["main_genre"]
        yield {
            "classification_level": "main_genre",
            "name": genre["name"],
            "aliases": genre.get("aliases", []),
            "master_folder": master["name"],
            "main_genre": genre["name"],
            "subgenre": None,
            "spotify_playlist": genre.get("spotify_playlist"),
            "local_folder": effective_local_folder(placement, project_root=project_root),
            "node": placement,
            "playlist_node": genre,
        }
        for subgenre in placement.get("subgenres", []):
            yield {
                "classification_level": "subgenre",
                "name": subgenre["name"],
                "aliases": subgenre.get("aliases", []),
                "master_folder": master["name"],
                "main_genre": genre["name"],
                "subgenre": subgenre["name"],
                "spotify_playlist": subgenre.get("spotify_playlist"),
                "local_folder": effective_local_folder(subgenre, project_root=project_root),
                "node": subgenre,
                "playlist_node": subgenre,
            }


def destination_index(schema: dict) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Index destinations by (master, level, normalized canonical/alias label)."""
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for destination in iter_destinations(schema):
        for label in [destination["name"], *destination.get("aliases", [])]:
            result[(
                canonical_key(destination["master_folder"]),
                destination["classification_level"],
                canonical_key(label),
            )] = destination
    return result


def resolve_destination(
    schema: dict,
    *,
    master_folder: str,
    main_genre: str,
    subgenre: str | None,
) -> dict[str, Any] | None:
    """Resolve a reviewed classification to its leaf or parent destination."""
    level = "subgenre" if subgenre else "main_genre"
    label = subgenre or main_genre
    return destination_index(schema).get(
        (canonical_key(master_folder), level, canonical_key(label))
    )


def validate_schema(schema: dict) -> None:
    """Validate v2 hierarchy references and global canonical/alias uniqueness."""
    if not is_v2(schema):
        return

    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_labels: dict[str, str] = {}

    def register(node: dict, kind: str) -> None:
        node_id = node.get("id")
        name = node.get("name")
        if not node_id or not name:
            errors.append(f"every {kind} requires id and name")
            return
        if node_id in seen_ids:
            errors.append(f"duplicate taxonomy id {node_id!r}")
        seen_ids.add(node_id)
        for label in [name, *node.get("aliases", [])]:
            key = canonical_key(label)
            owner = seen_labels.get(key)
            if owner and owner != node_id:
                errors.append(f"taxonomy label/alias {label!r} duplicates {owner!r}")
            else:
                seen_labels[key] = node_id

    genre_ids: set[str] = set()
    for genre in schema.get("main_genres", []):
        register(genre, "main genre")
        genre_ids.add(genre.get("id"))

    placement_keys: set[tuple[str, str]] = set()
    for context in iter_genre_placements(schema):
        master = context["master_folder"]
        placement = context["placement"]
        genre_id = placement.get("main_genre_id")
        if genre_id not in genre_ids:
            errors.append(
                f"placement in {master.get('name')!r} references unknown main genre {genre_id!r}"
            )
        placement_key = (master.get("id"), genre_id)
        if placement_key in placement_keys:
            errors.append(f"duplicate main-genre placement {placement_key!r}")
        placement_keys.add(placement_key)
        if not placement.get("local_folder"):
            errors.append(f"placement {placement_key!r} requires local_folder")
        for subgenre in placement.get("subgenres", []):
            register(subgenre, "subgenre")
            if not subgenre.get("local_folder"):
                errors.append(f"subgenre {subgenre.get('name')!r} requires local_folder")

    if errors:
        raise ValueError("Schema validation failed:\n- " + "\n- ".join(errors))
