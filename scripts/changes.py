#!/usr/bin/env python3
"""Compute what changed between two states of the published campus data.

A state is read from exactly the files a consumer downloads — `map.json`,
`map.geojson`, the context layers in `data/` and `routing.json` — and from
nothing else. A report built from scraper internals or intermediate results
could describe a change that never shipped, or miss one that did; reading the
published files means the report cannot say anything the release does not.

A state comes either from the working tree or from a git revision, so the same
code compares a fresh build against the last release.
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from geometry import distance_m
from sources import SOURCES

# Water is a place layer, but it is also drawn as background, so a change to it
# changes the picture even where no place's record changed.
CONTEXT_SLUGS = sorted(
    {source.slug for source in SOURCES if source.role == "context"} | {"water"}
)


@dataclass(frozen=True)
class State:
    places: dict[str, dict]  # id -> `map.json` record
    shapes: dict[str, dict]  # id -> GeoJSON geometry of the place itself
    context: dict[str, list[dict]]  # slug -> geometries of a background layer
    routing: bytes  # raw `routing.json`, empty when absent


def read_file(root: Path, revision: str | None, path: str) -> bytes:
    """Read a file from the working tree, or as committed at `revision`.

    A file that does not exist there reads as empty, so a state before the file
    was first published compares as having nothing in it.
    """
    if revision is None:
        try:
            return (root / path).read_bytes()
        except FileNotFoundError:
            return b""
    resolved = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "rev-parse",
            "--verify",
            "--quiet",
            f"{revision}^{{commit}}",
        ],
        capture_output=True,
        check=False,
    )
    if resolved.returncode != 0:
        # An unresolvable revision is not an empty state: reading it as one
        # would report every place as newly added.
        raise ValueError(f"cannot resolve revision {revision!r} in {root}")
    shown = subprocess.run(
        ["git", "-C", str(root), "show", f"{revision}:{path}"],
        capture_output=True,
        check=False,
    )
    return shown.stdout if shown.returncode == 0 else b""


def read_json(root: Path, revision: str | None, path: str, empty: Any) -> Any:
    """Parse a JSON file as `read_file` reads it, or return `empty` if absent."""
    content = read_file(root, revision, path)
    return json.loads(content) if content else empty


def place_shape(geometry: dict) -> dict:
    """The geometry that is the place itself, not its label anchor.

    A place's geometry is a collection of its shape followed by a label point;
    a point-only place has just the point, which is then its shape.
    """
    members = geometry["geometries"]
    return next((g for g in members if g["type"] != "Point"), members[0])


def read_state(root: Path, revision: str | None) -> State:
    """Read the published files from the working tree (`None`) or a revision."""
    records = read_json(root, revision, "map.json", [])
    collection = read_json(root, revision, "map.geojson", {"features": []})
    context = {
        slug: [
            feature["geometry"]
            for feature in read_json(
                root, revision, f"data/{slug}.geojson", {"features": []}
            )["features"]
        ]
        for slug in CONTEXT_SLUGS
    }
    return State(
        places={record["id"]: record for record in records},
        shapes={
            feature["id"]: place_shape(feature["geometry"])
            for feature in collection["features"]
        },
        context=context,
        routing=read_file(root, revision, "routing.json"),
    )


# Ids derive from names, so renaming a place removes one id and adds another.
# A removed and an added place this close, of the same kind, are the same place.
RENAME_REACH_M = 25

# Both derive from the geometry, which is reported as a move rather than as a
# field that changed.
GEOMETRY_FIELDS = {"center", "outline"}

KIND_ORDER = {"added": 0, "removed": 1, "renamed": 2, "changed": 3}


@dataclass(frozen=True)
class Move:
    """How far a place's geometry moved; Task 3 measures it."""

    dropped: tuple = ()
    added: tuple = ()
    dropped_m: float = 0.0
    added_m: float = 0.0


@dataclass(frozen=True)
class FieldChange:
    field: str
    before: object
    after: object


@dataclass(frozen=True)
class PlaceChange:
    kind: str  # "added" | "removed" | "renamed" | "changed"
    before_id: str | None
    after_id: str | None
    name: str  # the name after the change, else the name before it
    fields: tuple[FieldChange, ...] = ()
    before_shape: dict | None = None
    after_shape: dict | None = None
    move: Move | None = None


@dataclass(frozen=True)
class ContextChange:
    slug: str
    added: tuple[dict, ...]
    removed: tuple[dict, ...]


@dataclass(frozen=True)
class ChangeSet:
    places: tuple[PlaceChange, ...]
    context: tuple[ContextChange, ...]
    routing_changed: bool

    @property
    def is_empty(self) -> bool:
        """Whether nothing a reader of the map would see changed.

        Routing is for navigation, not for the map, so a change to it alone
        does not make a report worth publishing.
        """
        return not self.places and not self.context


def field_changes(before: dict, after: dict) -> tuple[FieldChange, ...]:
    keys = sorted((before.keys() | after.keys()) - GEOMETRY_FIELDS)
    return tuple(
        FieldChange(key, before.get(key), after.get(key))
        for key in keys
        if before.get(key) != after.get(key)
    )


def metres_apart(first: dict, second: dict) -> float:
    """Distance between two records' centers, which are latitude first."""
    lat, lon = second["center"]
    point = {"type": "Point", "coordinates": [lon, lat]}
    return distance_m([first["center"][1], first["center"][0]], point)


def pair_renames(
    before: State, after: State, removed: list[str], added: list[str]
) -> dict[str, str]:
    """Pair each removed id with the nearest added id of the same categories.

    Greedy in id order, so the same two states always pair the same way.
    """
    pairs: dict[str, str] = {}
    free = set(added)
    for old_id in removed:
        old = before.places[old_id]
        candidates = [
            (metres_apart(old, after.places[new_id]), new_id)
            for new_id in sorted(free)
            if after.places[new_id].get("categories", {}).keys()
            == old.get("categories", {}).keys()
        ]
        near = [c for c in candidates if c[0] <= RENAME_REACH_M]
        if near:
            _, new_id = min(near)
            pairs[old_id] = new_id
            free.discard(new_id)
    return pairs


def compare_places(before: State, after: State) -> list[PlaceChange]:
    removed = sorted(before.places.keys() - after.places.keys())
    added = sorted(after.places.keys() - before.places.keys())
    renames = pair_renames(before, after, removed, added)
    renamed_to = set(renames.values())
    changes = [
        PlaceChange(
            "renamed",
            old_id,
            new_id,
            after.places[new_id]["name"],
            field_changes(before.places[old_id], after.places[new_id]),
            before.shapes.get(old_id),
            after.shapes.get(new_id),
        )
        for old_id, new_id in renames.items()
    ]
    changes += [
        PlaceChange(
            "removed",
            old_id,
            None,
            before.places[old_id]["name"],
            before_shape=before.shapes.get(old_id),
        )
        for old_id in removed
        if old_id not in renames
    ]
    changes += [
        PlaceChange(
            "added",
            None,
            new_id,
            after.places[new_id]["name"],
            after_shape=after.shapes.get(new_id),
        )
        for new_id in added
        if new_id not in renamed_to
    ]
    for place_id in sorted(before.places.keys() & after.places.keys()):
        fields = field_changes(before.places[place_id], after.places[place_id])
        if fields:
            changes.append(
                PlaceChange(
                    "changed",
                    place_id,
                    place_id,
                    after.places[place_id]["name"],
                    fields,
                    before.shapes.get(place_id),
                    after.shapes.get(place_id),
                )
            )
    return sorted(
        changes,
        key=lambda c: (KIND_ORDER[c.kind], c.name, c.after_id or c.before_id),
    )


def compare_context(before: State, after: State) -> list[ContextChange]:
    changes = []
    for slug in sorted(before.context.keys() | after.context.keys()):
        old = {json.dumps(g, sort_keys=True) for g in before.context.get(slug, [])}
        new = {json.dumps(g, sort_keys=True) for g in after.context.get(slug, [])}
        if old != new:
            changes.append(
                ContextChange(
                    slug,
                    tuple(json.loads(g) for g in sorted(new - old)),
                    tuple(json.loads(g) for g in sorted(old - new)),
                )
            )
    return changes


def compare(before: State, after: State) -> ChangeSet:
    """Everything that differs between two states, in a stable order."""
    return ChangeSet(
        places=tuple(compare_places(before, after)),
        context=tuple(compare_context(before, after)),
        routing_changed=before.routing != after.routing,
    )
