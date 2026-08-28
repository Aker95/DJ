"""Canonical taxonomy lookup, validation, routing, and accepted updates."""

from __future__ import annotations

import copy
import json
import re
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .io import write_json


def canonical_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    plain = decomposed.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", plain.casefold()).strip()


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", canonical_key(value)).strip("-")


def load_schema(path: Path) -> dict:
    schema = json.loads(path.read_text(encoding="utf-8"))
    validate_schema(schema)
    return schema


def save_schema(schema: dict, path: Path) -> None:
    validate_schema(schema)
    write_json(path, schema)


def master_index(schema: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for master in schema.get("master_folders", []):
        for label in [master.get("name", ""), *master.get("aliases", [])]:
            if label:
                result[canonical_key(label)] = master
    return result


def main_genre_index(schema: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for genre in schema.get("main_genres", []):
        for label in [genre.get("name", ""), *genre.get("aliases", [])]:
            if label:
                result[canonical_key(label)] = genre
    return result


def iter_placements(schema: dict) -> Iterator[dict[str, Any]]:
    genres = {genre["id"]: genre for genre in schema.get("main_genres", [])}
    for master in schema.get("master_folders", []):
        for placement in master.get("genre_placements", []):
            genre = genres.get(placement.get("main_genre_id"))
            if genre:
                yield {"master": master, "genre": genre, "placement": placement}


def iter_subgenres(schema: dict) -> Iterator[dict[str, Any]]:
    for context in iter_placements(schema):
        for subgenre in context["placement"].get("subgenres", []):
            yield {**context, "subgenre": subgenre}


def playlist_name(master: dict, genre: dict, subgenre: dict | None = None) -> str:
    labels = [master.get("spotify_name_prefix") or master["name"], genre["name"]]
    if subgenre:
        labels.append(subgenre["name"])
    return " • ".join(labels)


def placement_id(master: dict, genre: dict, subgenre: dict | None = None) -> str:
    parts = [master["id"], genre["id"]]
    if subgenre:
        parts.append(subgenre["id"])
    return ":".join(parts)


def iter_destinations(schema: dict, project_root: Path | None = None) -> Iterator[dict]:
    for context in iter_placements(schema):
        master, genre, placement = context["master"], context["genre"], context["placement"]
        yield _destination(master, genre, placement, None, project_root)
        for subgenre in placement.get("subgenres", []):
            yield _destination(master, genre, placement, subgenre, project_root)


def _destination(
    master: dict, genre: dict, placement: dict, subgenre: dict | None, project_root: Path | None
) -> dict:
    node = subgenre or placement
    local_folder = node.get("local_folder")
    if project_root and local_folder:
        local_folder = project_root / local_folder
    return {
        "placement_id": placement_id(master, genre, subgenre),
        "master_folder": master["name"],
        "main_genre": genre["name"],
        "subgenre": subgenre["name"] if subgenre else None,
        "classification_level": "subgenre" if subgenre else "main_genre",
        "bpm_min": master["bpm_min"],
        "bpm_max": master["bpm_max"],
        "bpm_max_inclusive": bool(master.get("bpm_max_inclusive")),
        "local_folder": local_folder,
        "spotify_playlist": node.get("spotify_playlist"),
        "spotify_playlist_name": node.get("spotify_playlist_name")
        or playlist_name(master, genre, subgenre),
        "node": node,
    }


def resolve_destination(
    schema: dict, master_folder: str, main_genre: str, subgenre: str | None = None
) -> dict | None:
    wanted_master = master_index(schema).get(canonical_key(master_folder))
    wanted_genre = main_genre_index(schema).get(canonical_key(main_genre))
    if not wanted_master or not wanted_genre:
        return None
    for destination in iter_destinations(schema):
        if not destination["placement_id"].startswith(f"{wanted_master['id']}:{wanted_genre['id']}"):
            continue
        if subgenre is None and destination["subgenre"] is None:
            return destination
        labels = [destination.get("subgenre") or "", *destination["node"].get("aliases", [])]
        if subgenre and canonical_key(subgenre) in {canonical_key(label) for label in labels}:
            return destination
    return None


def bpm_in_master(master: dict, bpm: float | int) -> bool:
    value = float(bpm)
    minimum, maximum = float(master["bpm_min"]), float(master["bpm_max"])
    if master.get("bpm_max_inclusive"):
        return minimum <= value <= maximum
    return minimum <= value < maximum


def bpm_master(schema: dict, bpm: float | int | None) -> dict | None:
    if bpm is None:
        return None
    return next((master for master in schema["master_folders"] if bpm_in_master(master, bpm)), None)


def validate_schema(schema: dict) -> None:
    """Reject broken IDs, labels, routes, paths, or overlapping BPM bands."""

    if not str(schema.get("_meta", {}).get("version", "")).startswith("3."):
        raise ValueError("The schema must use version 3")
    errors: list[str] = []
    labels: dict[str, str] = {}
    global_ids: set[str] = set()

    def register(node: dict, kind: str, repeated_id: bool = False) -> None:
        identifier, name = node.get("id"), node.get("name")
        if not identifier or not name:
            errors.append(f"Every {kind} requires id and name")
            return
        if identifier in global_ids and not repeated_id:
            errors.append(f"Duplicate taxonomy id {identifier!r}")
        global_ids.add(identifier)
        for label in [name, *node.get("aliases", [])]:
            key = canonical_key(label)
            owner = labels.get(key)
            if owner and owner != identifier:
                errors.append(f"Taxonomy label {label!r} conflicts with {owner!r}")
            labels[key] = identifier

    genres = schema.get("main_genres", [])
    for genre in genres:
        register(genre, "main genre")
    genre_ids = {genre.get("id") for genre in genres}
    masters = sorted(schema.get("master_folders", []), key=lambda item: item.get("bpm_min", 0))
    for index, master in enumerate(masters):
        register(master, "master folder")
        try:
            minimum, maximum = float(master["bpm_min"]), float(master["bpm_max"])
            if minimum >= maximum:
                errors.append(f"Master {master['name']!r} has an empty BPM interval")
            if index:
                prior = masters[index - 1]
                if minimum < float(prior["bpm_max"]) or (
                    minimum == float(prior["bpm_max"]) and prior.get("bpm_max_inclusive")
                ):
                    errors.append("Master-folder BPM intervals overlap")
        except (KeyError, TypeError, ValueError):
            errors.append(f"Master {master.get('name')!r} requires valid BPM bounds")

    subgenre_signatures: dict[str, tuple] = {}
    seen_placements: set[tuple[str, str]] = set()
    for context in iter_placements(schema):
        master, genre, placement = context["master"], context["genre"], context["placement"]
        key = (master["id"], genre["id"])
        if genre["id"] not in genre_ids or key in seen_placements:
            errors.append(f"Invalid or duplicate placement {key!r}")
        seen_placements.add(key)
        if not placement.get("local_folder"):
            errors.append(f"Placement {key!r} requires local_folder")
        for subgenre in placement.get("subgenres", []):
            signature = (subgenre.get("name"), tuple(sorted(subgenre.get("aliases", []))))
            previous = subgenre_signatures.get(subgenre.get("id"))
            if previous is None:
                subgenre_signatures[subgenre.get("id")] = signature
                register(subgenre, "subgenre")
            elif previous != signature:
                errors.append(f"Subgenre {subgenre.get('id')!r} has conflicting labels")
            if not subgenre.get("local_folder"):
                errors.append(f"Subgenre {subgenre.get('name')!r} requires local_folder")
    if errors:
        raise ValueError("Schema validation failed:\n- " + "\n- ".join(errors))


def apply_schema_changes(schema: dict, changes: list[dict]) -> tuple[dict, list[dict]]:
    """Return an updated schema and an idempotent report for accepted changes."""

    updated = copy.deepcopy(schema)
    report: list[dict] = []
    for change in changes:
        action = change.get("action")
        if action == "add_alias":
            result = _add_alias(updated, change)
        elif action == "ensure_main_genre":
            result = _ensure_main_genre(updated, change)
        elif action == "ensure_subgenre":
            result = _ensure_subgenre(updated, change)
        else:
            raise ValueError(f"Unsupported schema action {action!r}")
        report.append({"action": action, **result})
    validate_schema(updated)
    return updated, report


def _all_nodes(schema: dict) -> list[dict]:
    nodes = [*schema.get("master_folders", []), *schema.get("main_genres", [])]
    nodes.extend(context["subgenre"] for context in iter_subgenres(schema))
    return nodes


def _add_alias(schema: dict, change: dict) -> dict:
    canonical, alias = change.get("canonical_name", ""), change.get("alias", "")
    wanted = canonical_key(canonical)
    matches = [
        node
        for node in _all_nodes(schema)
        if wanted
        in {
            canonical_key(node.get("name", "")),
            *(canonical_key(value) for value in node.get("aliases", [])),
        }
    ]
    if not matches or not alias:
        raise ValueError(f"Invalid alias change: {change!r}")
    alias_owner = next(
        (
            node
            for node in _all_nodes(schema)
            if canonical_key(alias)
            in {canonical_key(node.get("name", "")), *(canonical_key(v) for v in node.get("aliases", []))}
        ),
        None,
    )
    if alias_owner and alias_owner.get("id") != matches[0].get("id"):
        raise ValueError(f"Alias {alias!r} already belongs to {alias_owner.get('name')!r}")
    changed = False
    for node in matches:
        if canonical_key(alias) not in {canonical_key(value) for value in node.get("aliases", [])}:
            node.setdefault("aliases", []).append(alias)
            changed = True
    return {"name": canonical, "alias": alias, "changed": changed}


def _ensure_main_genre(schema: dict, change: dict) -> dict:
    name, master_name = change.get("name", ""), change.get("master_folder", "")
    master = master_index(schema).get(canonical_key(master_name))
    if not name or not master:
        raise ValueError(f"Invalid main-genre change: {change!r}")
    genre = main_genre_index(schema).get(canonical_key(name))
    created = genre is None
    if genre is None:
        genre = {
            "id": change.get("id") or slug(name),
            "name": name,
            "aliases": change.get("aliases", []),
            "spotify_playlist": None,
            "notes": change.get("rationale") or change.get("notes"),
        }
        schema["main_genres"].append(genre)
    placement = next(
        (item for item in master.get("genre_placements", []) if item["main_genre_id"] == genre["id"]),
        None,
    )
    routed = placement is None
    if routed:
        placement = {
            "main_genre_id": genre["id"],
            "local_folder": f"{master['local_path']}/{slug(genre['name']).replace('-', ' ')}",
            "spotify_playlist": None,
            "subgenres": [],
        }
        master.setdefault("genre_placements", []).append(placement)
    return {"name": genre["name"], "created": created, "placement_added": routed}


def _ensure_subgenre(schema: dict, change: dict) -> dict:
    name = change.get("name", "")
    master = master_index(schema).get(canonical_key(change.get("master_folder", "")))
    genre = main_genre_index(schema).get(canonical_key(change.get("main_genre", "")))
    if not name or not master or not genre:
        raise ValueError(f"Invalid subgenre change: {change!r}")
    placement = next(
        (item for item in master.get("genre_placements", []) if item["main_genre_id"] == genre["id"]),
        None,
    )
    if placement is None:
        _ensure_main_genre(schema, {"name": genre["name"], "master_folder": master["name"]})
        placement = next(
            item for item in master["genre_placements"] if item["main_genre_id"] == genre["id"]
        )
    existing_contexts = [
        context
        for context in iter_subgenres(schema)
        if canonical_key(name)
        in {
            canonical_key(context["subgenre"]["name"]),
            *(canonical_key(alias) for alias in context["subgenre"].get("aliases", [])),
        }
    ]
    if existing_contexts and any(context["genre"]["id"] != genre["id"] for context in existing_contexts):
        raise ValueError(f"Subgenre {name!r} already belongs to another main genre")
    existing_here = next(
        (
            item
            for item in placement.get("subgenres", [])
            if canonical_key(name)
            in {canonical_key(item["name"]), *(canonical_key(a) for a in item.get("aliases", []))}
        ),
        None,
    )
    if existing_here:
        return {"name": existing_here["name"], "created": False, "placement_added": False}
    source = existing_contexts[0]["subgenre"] if existing_contexts else None
    node = {
        "id": source["id"] if source else change.get("id") or slug(name),
        "name": source["name"] if source else name,
        "aliases": list(source.get("aliases", [])) if source else change.get("aliases", []),
        "local_folder": f"{placement['local_folder']}/{slug(source['name'] if source else name).replace('-', ' ')}",
        "spotify_playlist": None,
        "notes": source.get("notes") if source else change.get("rationale") or change.get("notes"),
    }
    placement.setdefault("subgenres", []).append(node)
    return {"name": node["name"], "created": source is None, "placement_added": True}
