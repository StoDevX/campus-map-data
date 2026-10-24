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
    shown = subprocess.run(
        ["git", "-C", str(root), "show", f"{revision}:{path}"],
        capture_output=True,
        check=False,
    )
    return shown.stdout if shown.returncode == 0 else b""


def read_json(root: Path, revision: str | None, path: str, empty):
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
