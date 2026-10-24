#!/usr/bin/env python3
"""Describe what changed in the campus data, for someone deciding to publish it.

A release changes hundreds of places at once, and a list of ids does not say
whether the change is the one that was intended. The report groups the changes
into areas of the campus and summarises them in a sentence, so a reader can
see at a glance what moved and where, and a surprise stands out.

The report is deterministic: the same two states give the same words in the
same order, so republishing an unchanged release produces no difference. It
draws only from the published files, through `changes.compare`, and so says
nothing the release itself does not.
"""

import argparse
import math
import sys
from pathlib import Path

from changes import ChangeSet, PlaceChange, compare, read_state
from geometry import (
    METRES_PER_DEGREE_LAT,
    METRES_PER_DEGREE_LON_AT_EQUATOR,
    bbox,
    distance_m,
    positions,
)

# Places whose moves are this close, directly or through a chain of others,
# are shown together in one close-up.
CLUSTER_REACH_M = 150

# More areas than this are too many close-ups to read; the report shows this
# many and lists the rest.
MAX_CLOSEUPS = 12

# Added points beyond this many are a new layer rather than separate edits.
NEW_LAYER_SIZE = 20

# Names listed in a sentence, and the largest group worth naming at all.
NAMES_SHOWN = 3
NAMED_GROUP_LIMIT = 10

CATEGORY_WORDS = {
    "bike-rack": "bike rack",
    "building": "building",
    "parking": "parking lot",
    "trail": "trail",
}
PLURALS = {
    "bike rack": "bike racks",
    "building": "buildings",
    "parking lot": "parking lots",
    "trail": "trails",
}


def category_word(change: PlaceChange) -> str:
    """The singular word for what kind of place this is.

    A place has several categories, such as a trail's `outdoors` and `trail`;
    the one with a word of its own says best what kind of place it is.
    """
    if not change.categories:
        return "place"
    known = [slug for slug in change.categories if slug in CATEGORY_WORDS]
    slug = (known or change.categories)[0]
    return CATEGORY_WORDS.get(slug, slug.replace("-", " "))


def pluralise(word: str, count: int) -> str:
    if count == 1:
        return word
    return PLURALS.get(word, word + "s")


def changed_shape(change: PlaceChange) -> bool:
    return (
        change.kind in ("changed", "renamed")
        and change.move is not None
        and change.move.visible
    )


def area_geometry(change: PlaceChange) -> dict | None:
    """Where a change shows on the map: its moved stretches, or its whole shape."""
    if change.move is not None:
        stretches = [*change.move.dropped, *change.move.added]
        return {
            "type": "GeometryCollection",
            "geometries": [
                {"type": "LineString", "coordinates": [list(p) for p in stretch]}
                if len(stretch) > 1
                else {"type": "Point", "coordinates": list(stretch[0])}
                for stretch in stretches
            ],
        }
    return change.after_shape or change.before_shape


def within_reach(first: dict | None, second: dict | None) -> bool:
    """Whether any position of `first` is within reach of `second`.

    It stops at the first that is: a move's stretches are sampled every metre,
    and measuring every position against every other is quadratic in both.
    """
    return any(
        distance_m(position, second) <= CLUSTER_REACH_M for position in positions(first)
    )


def boxes_out_of_reach(first: dict | None, second: dict | None) -> bool:
    """Whether two shapes' bounding boxes are further apart than the reach.

    If they are, no position of one can be within reach of the other, so the
    exact measure can be skipped. A degree of longitude is taken at the box
    edge nearest a pole, where it is shortest, so the test never says apart
    for shapes that are near.
    """
    a, b = bbox(first), bbox(second)
    if a is None or b is None:
        return True
    lon_gap = max(a[0] - b[2], b[0] - a[2], 0.0)
    lat_gap = max(a[1] - b[3], b[1] - a[3], 0.0)
    shortest = math.cos(math.radians(max(abs(v) for v in (a[1], a[3], b[1], b[3]))))
    lon_m = lon_gap * shortest * METRES_PER_DEGREE_LON_AT_EQUATOR
    return max(lon_m, lat_gap * METRES_PER_DEGREE_LAT) > CLUSTER_REACH_M


def near(first: dict | None, second: dict | None) -> bool:
    if boxes_out_of_reach(first, second):
        return False
    return within_reach(first, second) or within_reach(second, first)


def cluster(members: list[PlaceChange]) -> list[list[PlaceChange]]:
    """Group places so that any chain of places within reach shares a group."""
    geometries = [area_geometry(member) for member in members]
    unvisited = set(range(len(members)))
    groups = []
    while unvisited:
        frontier = [unvisited.pop()]
        group = []
        while frontier:
            current = frontier.pop()
            group.append(current)
            linked = [i for i in unvisited if near(geometries[current], geometries[i])]
            unvisited.difference_update(linked)
            frontier.extend(linked)
        groups.append([members[i] for i in sorted(group)])
    return groups


def first_position(group: list[PlaceChange]) -> tuple[float, float]:
    """Sort key putting the northernmost, then westernmost, position first."""
    found = [
        position for member in group for position in positions(area_geometry(member))
    ]
    lon, lat = min(found, key=lambda p: (-p[1], p[0]))
    return -lat, lon


def is_point(change: PlaceChange) -> bool:
    shape = change.after_shape
    return shape is not None and shape["type"] == "Point"


def areas(changes: ChangeSet) -> list[list[PlaceChange]]:
    """Places that show on the map, grouped by where they are.

    Places changed only in text have nothing to show, so they have no area.
    """
    shown = [
        change
        for change in changes.places
        if change.kind in ("added", "removed") or changed_shape(change)
    ]
    layer = [c for c in shown if c.kind == "added" and is_point(c)]
    groups = []
    if len(layer) > NEW_LAYER_SIZE:
        groups.append(layer)
        shown = [c for c in shown if c not in layer]
    groups += cluster(shown)
    return sorted(groups, key=first_position)


def names_of(group: list[PlaceChange]) -> str:
    """Up to three names in name order, for a group small enough to name."""
    if len(group) > NAMED_GROUP_LIMIT:
        return ""
    names = sorted(change.name for change in group)
    listed = ", ".join(names[:NAMES_SHOWN])
    if len(names) > NAMES_SHOWN:
        listed = f"{listed} and {len(names) - NAMES_SHOWN} more"
    return f": {listed}"


def sentence_kind(change: PlaceChange) -> str:
    """Which part of the summary a change belongs to."""
    if change.kind == "changed":
        return "changed shape" if changed_shape(change) else "changed"
    return change.kind


SENTENCE_ORDER = ["added", "removed", "renamed", "changed shape", "changed"]


def summary(changes: ChangeSet) -> str:
    """A plain paragraph of counts by kind and category, naming a few places."""
    groups: dict[tuple[str, str], list[PlaceChange]] = {}
    for change in changes.places:
        key = (sentence_kind(change), category_word(change))
        groups.setdefault(key, []).append(change)
    sentences = [
        f"{len(group)} {pluralise(word, len(group))} {phrase}{names_of(group)}."
        for phrase in SENTENCE_ORDER
        for (kind, word), group in sorted(groups.items())
        if kind == phrase
    ]
    if changes.routing_changed:
        sentences.append("The walking routes were rebuilt to match.")
    return " ".join(sentences)


def release_notes(changes: ChangeSet, report_url: str, commit_url: str) -> str:
    """Markdown for a release: the summary, counts, and where to read more."""
    counts = {
        "Added": sum(c.kind == "added" for c in changes.places),
        "Removed": sum(c.kind == "removed" for c in changes.places),
        "Renamed": sum(c.kind == "renamed" for c in changes.places),
        "Changed": sum(c.kind == "changed" for c in changes.places),
    }
    rows = [f"| {label} | {count} |" for label, count in counts.items()]
    return "\n".join(
        [
            summary(changes),
            "",
            "| Places | Count |",
            "| --- | ---: |",
            *rows,
            "",
            f"[Full change report]({report_url})",
            "",
            f"[Commit]({commit_url})",
            "",
        ]
    )


def write(path: Path, text: str) -> None:
    """Write `text` to `path`, creating the directories above it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Write the change report between two states; say so when nothing changed."""
    # Imported here because the page module builds on this one.
    from report_page import render

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, help="revision to compare from")
    parser.add_argument(
        "--after", help="revision to compare to (default: working tree)"
    )
    parser.add_argument("--date", required=True, help="date shown on the report")
    parser.add_argument(
        "--out", type=Path, required=True, help="where to write the page"
    )
    parser.add_argument("--notes", type=Path, help="where to write the release notes")
    parser.add_argument("--report-url", default="", help="link to the published page")
    parser.add_argument("--commit-url", default="", help="link to the commit")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="the repository to read",
    )
    args = parser.parse_args(argv)

    try:
        before = read_state(args.root, args.before)
        after = read_state(args.root, args.after)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2

    changes = compare(before, after)
    if changes.is_empty:
        print("No change to the published data.")
        return 0

    write(args.out, render(changes, before, after, args.date))
    if args.notes:
        write(args.notes, release_notes(changes, args.report_url, args.commit_url))
    print(summary(changes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
