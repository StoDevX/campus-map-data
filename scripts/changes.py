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

import collections
import difflib
import itertools
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from build import LAYER_PROPERTY, name_for, source_index
from geometry import distance_m, lines, polygons, positions
from sources import SOURCES

# Every layer has background: all of a context layer, and the unnamed features
# of a place layer, which `build.py` publishes as geometry only.
CONTEXT_SLUGS = sorted({source.slug for source in SOURCES})

# Layers whose named features are places. A change to one is reported as a
# place, so comparing its geometry as background too would count it twice.
PLACE_SLUGS = {source.slug for source in SOURCES if source.role == "place"}

SOURCES_BY_TITLE = source_index()


@dataclass(frozen=True)
class State:
    places: dict[str, dict]  # id -> `map.json` record
    shapes: dict[str, dict]  # id -> GeoJSON geometry of the place itself
    context: dict[str, list[dict]]  # slug -> background geometries of a layer
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


def is_background(slug: str, feature: dict) -> bool:
    """Whether a feature is a background shape rather than a place or nothing.

    A feature with no coordinates has no shape to compare or draw. In a place
    layer, only a feature `build.py` finds no name for stays geometry only.
    """
    if not positions(feature["geometry"]):
        return False
    if slug not in PLACE_SLUGS:
        return True
    properties = feature["properties"]
    return name_for(SOURCES_BY_TITLE[properties[LAYER_PROPERTY]], properties) is None


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
            if is_background(slug, feature)
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


# A stretch of a line or ring this far from the other shape was drawn somewhere
# else; closer than this is survey noise, not an edit.
MOVE_REACH_M = 3.0

# How densely lines are sampled when judging which parts moved.
SAMPLE_SPACING_M = 1.0

Stretch = tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Move:
    """The stretches of a place's outline that no longer coincide with it.

    `dropped` are runs of the old shape that the new one no longer covers and
    `added` are runs of the new shape that the old one did not; each run is
    consecutive positions, with its length in metres alongside.
    """

    dropped: tuple[Stretch, ...] = ()
    added: tuple[Stretch, ...] = ()
    dropped_m: float = 0.0
    added_m: float = 0.0

    @property
    def visible(self) -> bool:
        """Whether any stretch moved far enough to show on a map."""
        return bool(self.dropped or self.added)


def outlines(geometry: dict | None) -> list[list]:
    """Every line of a geometry, with each polygon ring as a closed line.

    A point is a one-position line, so it can be compared like any other shape.
    """
    found = lines(geometry) + [ring for rings in polygons(geometry) for ring in rings]
    if not found:
        found = [[position] for position in positions(geometry)]
    return found


def metres_between(a, b) -> float:
    return distance_m(list(a), {"type": "Point", "coordinates": list(b)})


def sampled(line: list) -> list[tuple[float, float]]:
    """Points along a line about `SAMPLE_SPACING_M` apart, vertices included."""
    points = [(round(line[0][0], 7), round(line[0][1], 7))]
    for a, b in itertools.pairwise(line):
        steps = max(1, math.ceil(metres_between(a, b)))
        points += [
            (
                round(a[0] + (b[0] - a[0]) * i / steps, 7),
                round(a[1] + (b[1] - a[1]) * i / steps, 7),
            )
            for i in range(1, steps + 1)
        ]
    return points


def off_runs(geometry: dict | None, other: dict | None) -> tuple[list[Stretch], float]:
    """Runs of `geometry` more than `MOVE_REACH_M` from `other`, and their metres.

    Lines are sampled densely, because a long stretch judged at one point can
    hide half of itself being deleted. Distance is to the other shape's outline
    rather than its area, so a footprint redrawn inside its old outline still
    shows its new edges. A run's metres are the sampled length of its off part.
    """
    reference = other
    other_lines = [line for line in outlines(other) if len(line) > 1]
    if other_lines:
        reference = {"type": "MultiLineString", "coordinates": other_lines}

    def off(point) -> bool:
        return reference is None or distance_m(point, reference) > MOVE_REACH_M

    runs: list[Stretch] = []
    metres = 0.0
    for line in outlines(geometry):
        if len(line) == 1:
            point = (round(line[0][0], 7), round(line[0][1], 7))
            if off(point):
                runs.append((point,))
            continue
        run: list = []
        for point in sampled(line) + [None]:
            if point is not None and off(point):
                run.append(point)
                continue
            if len(run) > 1:
                runs.append(tuple(run))
                metres += sum(metres_between(a, b) for a, b in itertools.pairwise(run))
            run = []
    return runs, metres


def moved(before: dict | None, after: dict | None) -> Move:
    """What of `before` and `after` the other shape does not lie along."""
    dropped, dropped_m = off_runs(before, after)
    added, added_m = off_runs(after, before)
    return Move(tuple(dropped), tuple(added), dropped_m, added_m)


def word_diff(before: str, after: str) -> tuple[tuple[str, str], ...]:
    """Runs of ("same" | "removed" | "added", words) turning `before` into `after`."""
    old, new = before.split(), after.split()
    labels = {"equal": "same", "delete": "removed", "insert": "added"}
    runs: list[tuple[str, str]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, old, new, autojunk=False
    ).get_opcodes():
        if op == "replace":
            runs.append(("removed", " ".join(old[i1:i2])))
            runs.append(("added", " ".join(new[j1:j2])))
        else:
            words = old[i1:i2] if op != "insert" else new[j1:j2]
            runs.append((labels[op], " ".join(words)))
    return tuple(runs)


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
    categories: tuple[str, ...] = ()  # the place's category slugs, in record order


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


def category_slugs(*records: dict) -> tuple[str, ...]:
    """The category slugs of the first record that has any."""
    return next((tuple(r["categories"]) for r in records if r.get("categories")), ())


def field_changes(before: dict, after: dict) -> tuple[FieldChange, ...]:
    """Each record field that differs, by name.

    The center and outline are left out: they follow from the shape, whose
    change is reported once, as a move, rather than as numbers that changed.
    """
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


def shape_changes(
    before: dict | None, after: dict | None
) -> tuple[tuple[FieldChange, ...], Move | None]:
    """The "geometry" field change and the move, or nothing if the shape is the same."""
    if before == after:
        return (), None
    return (FieldChange("geometry", before, after),), moved(before, after)


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


def place_change(
    kind: str,
    before_id: str,
    after_id: str,
    old: dict,
    new: dict,
    before_shape: dict | None,
    after_shape: dict | None,
) -> PlaceChange:
    """A place present on both sides, with its field changes and its move."""
    shape_fields, move = shape_changes(before_shape, after_shape)
    fields = tuple(
        sorted(field_changes(old, new) + shape_fields, key=lambda f: f.field)
    )
    return PlaceChange(
        kind,
        before_id,
        after_id,
        new["name"],
        fields,
        before_shape,
        after_shape,
        move,
        category_slugs(new, old),
    )


def compare_places(before: State, after: State) -> list[PlaceChange]:
    """Every place added, removed, renamed or changed, in a stable order.

    Ids come from names, so a rename looks like a removal and an addition;
    pairing the two near each other says what a reader would: it was renamed.
    """
    removed = sorted(before.places.keys() - after.places.keys())
    added = sorted(after.places.keys() - before.places.keys())
    renames = pair_renames(before, after, removed, added)
    renamed_to = set(renames.values())
    changes = [
        place_change(
            "renamed",
            old_id,
            new_id,
            before.places[old_id],
            after.places[new_id],
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
            categories=category_slugs(before.places[old_id]),
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
            categories=category_slugs(after.places[new_id]),
        )
        for new_id in added
        if new_id not in renamed_to
    ]
    for place_id in sorted(before.places.keys() & after.places.keys()):
        change = place_change(
            "changed",
            place_id,
            place_id,
            before.places[place_id],
            after.places[place_id],
            before.shapes.get(place_id),
            after.shapes.get(place_id),
        )
        if change.fields:
            changes.append(change)
    return sorted(
        changes,
        key=lambda c: (KIND_ORDER[c.kind], c.name, c.after_id or c.before_id),
    )


def compare_context(before: State, after: State) -> list[ContextChange]:
    """The background shapes that appeared or disappeared, layer by layer.

    Background features have no ids, so a layer is compared as a multiset of
    geometries: a shape redrawn is one removed and one added, and deleting one
    of two identical copies is still one removed.
    """

    def counted(state: State, slug: str) -> collections.Counter:
        return collections.Counter(
            json.dumps(g, sort_keys=True) for g in state.context.get(slug, [])
        )

    changes = []
    for slug in sorted(before.context.keys() | after.context.keys()):
        old, new = counted(before, slug), counted(after, slug)
        if old != new:
            changes.append(
                ContextChange(
                    slug,
                    tuple(json.loads(g) for g in sorted((new - old).elements())),
                    tuple(json.loads(g) for g in sorted((old - new).elements())),
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
