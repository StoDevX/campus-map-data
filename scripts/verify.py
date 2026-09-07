#!/usr/bin/env python3
"""Assert the invariants that would otherwise fail silently downstream.

Everything here is a failure this pipeline can actually produce, and that
nothing else would catch: the scheduled run has nobody watching it, and the
files it commits are read by an app, not by a person who would notice that a
building had moved to Nebraska.

The two worth naming:

- **Coordinate order.** `map.json` is latitude-first and `map.geojson` is
  longitude-first (Carleton's inconsistency, reproduced on purpose). Swap
  either and every place lands in the Indian Ocean, with no error anywhere. A
  bounding-box check on both files, in their respective orders, is what makes
  that impossible to ship.

- **Label anchors inside their footprints.** An anchor that escapes its polygon
  labels the wrong building, which looks like correct data until someone tries
  to use the map.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import geometry
import yaml
from scrape import VOLATILE_FIELDS
from sources import SOURCES, slugs

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Manitou Heights and a generous margin. The campus is about 1.5 km across; this
# box is roughly 20 km, wide enough that a legitimate outlying parcel passes and
# tight enough that a coordinate swap or a projection mistake cannot.
CAMPUS = (-93.35, 44.35, -93.05, 44.55)

# Every key `carls-app/map-data`'s map.json carries. A consumer written against
# Carleton's data must not have to test for a missing key.
CARLETON_KEYS = frozenset(
    {
        "accessibility",
        "address",
        "categories",
        "center",
        "departments",
        "description",
        "floors",
        "id",
        "name",
        "offices",
        "outline",
        "photo",
    }
)

NUMBERED = re.compile(r"-\d+$")


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.notes: list[str] = []

    def check(self, condition: bool, message: str) -> bool:
        if not condition:
            self.failures.append(message)
        return condition

    def note(self, message: str) -> None:
        self.notes.append(message)


def in_campus(lon: float, lat: float) -> bool:
    west, south, east, north = CAMPUS
    return west <= lon <= east and south <= lat <= north


def verify_raw(report: Report) -> None:
    for slug in slugs():
        path = DATA / f"{slug}.geojson"
        if not report.check(path.exists(), f"data/{slug}.geojson is missing"):
            continue
        collection = json.loads(path.read_text())
        features = collection.get("features") or []
        report.check(
            collection.get("type") == "FeatureCollection",
            f"data/{slug}.geojson is not a FeatureCollection",
        )
        report.check(bool(features), f"data/{slug}.geojson has no features")

        leaked = {
            key
            for feature in features
            for key in (feature.get("properties") or {})
            if key in VOLATILE_FIELDS
        }
        report.check(
            not leaked,
            f"data/{slug}.geojson still carries volatile fields: {sorted(leaked)}",
        )

        for feature in features:
            for lon, lat in geometry.positions(feature.get("geometry")):
                if not in_campus(lon, lat):
                    report.check(
                        False,
                        f"data/{slug}.geojson has a point off campus: {[lon, lat]}",
                    )
                    break
            else:
                continue
            break


def verify_map_json(report: Report) -> list[dict]:
    records = json.loads((ROOT / "map.json").read_text())
    report.check(bool(records), "map.json is empty")

    ids = [record["id"] for record in records]
    report.check(len(ids) == len(set(ids)), "map.json has duplicate ids")

    for record in records:
        missing = CARLETON_KEYS - record.keys()
        report.check(not missing, f"{record['id']}: missing keys {sorted(missing)}")
        report.check(bool(record.get("name")), f"{record['id']}: has no name")

        center = record.get("center")
        if report.check(bool(center), f"{record['id']}: has no center"):
            lat, lon = center
            report.check(
                in_campus(lon, lat),
                f"{record['id']}: center {center} is off campus "
                f"(map.json is latitude-first — is it the wrong way round?)",
            )

        for point in record.get("outline") or []:
            if not in_campus(point[1], point[0]):
                report.check(
                    False, f"{record['id']}: outline point {point} is off campus"
                )
                break

    numbered = sorted({NUMBERED.sub("", i) for i in ids if NUMBERED.search(i)})
    if numbered:
        report.note(
            f"{len(numbered)} name(s) shared by several places, so their ids are "
            f"numbered: {', '.join(numbered)}. Give them real names in "
            f"overrides.yaml if the college has any."
        )
    return records


def verify_map_geojson(report: Report, records: list[dict]) -> None:
    collection = json.loads((ROOT / "map.geojson").read_text())
    features = collection.get("features") or []
    report.check(
        collection.get("type") == "FeatureCollection",
        "map.geojson is not a FeatureCollection",
    )
    report.check(
        [feature["id"] for feature in features] == [record["id"] for record in records],
        "map.geojson and map.json do not hold the same places, in the same order",
    )

    for feature in features:
        shape = feature.get("geometry") or {}
        report.check(
            shape.get("type") == "GeometryCollection",
            f"{feature['id']}: geometry is {shape.get('type')}, not a GeometryCollection",
        )
        members = shape.get("geometries") or []
        report.check(bool(members), f"{feature['id']}: GeometryCollection is empty")

        for lon, lat in geometry.positions(shape):
            if not in_campus(lon, lat):
                report.check(
                    False,
                    f"{feature['id']}: point {[lon, lat]} is off campus "
                    f"(map.geojson is longitude-first — is it the wrong way round?)",
                )
                break

        points = [member for member in members if member["type"] == "Point"]
        areas = [
            member
            for member in members
            if member["type"] in ("Polygon", "MultiPolygon")
        ]
        if points and areas:
            anchor = points[0]["coordinates"]
            inside = any(
                geometry.point_in_polygon(anchor, rings)
                for member in areas
                for rings in geometry.polygons(member)
            )
            report.check(
                inside,
                f"{feature['id']}: label anchor {anchor} is outside its own footprint "
                f"— set a centerpoint for it in overrides.yaml",
            )


def verify_overrides(report: Report, records: list[dict]) -> None:
    overrides = yaml.safe_load((ROOT / "overrides.yaml").read_text()) or {}
    known = {record["id"] for record in records}
    layers = set(slugs())

    for change in overrides.get("changes") or []:
        report.check(
            change["id"] in known,
            f"overrides.yaml: changes entry for unknown id {change['id']!r}",
        )

    # An `ids` entry that stopped applying is invisible in the output — the
    # place just quietly reverts to its default id — so the requested id must
    # actually exist. That also catches the entry mapping two places to one id:
    # both get numbered, and the bare id is then in nobody's hands.
    for slug, mapping in (overrides.get("ids") or {}).items():
        if not report.check(
            slug in layers, f"overrides.yaml: ids has no such layer {slug!r}"
        ):
            continue
        for name, mapped in (mapping or {}).items():
            report.check(
                mapped in known,
                f"overrides.yaml: ids entry {slug}/{name!r} -> {mapped!r} "
                f"produced no place with that id",
            )


def verify_routing(report: Report, records: list[dict]) -> None:
    """The routing graph, if one has been built.

    A router fails quietly in a way a map does not: an unreachable component
    does not draw wrong, it just never returns a path, and the caller cannot
    tell that from "no route exists". So connectivity is asserted here rather
    than discovered in the app.
    """
    path = ROOT / "routing.json"
    if not path.exists():
        report.note("routing.json not built — run scripts/build_routing.py")
        return

    graph = json.loads(path.read_text())
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    entrances = graph.get("entrances") or {}
    declared = {int(k) for k in (graph.get("edgeTypes") or {})}

    report.check(bool(nodes), "routing.json has no nodes")
    report.check(bool(edges), "routing.json has no edges")

    for index, point in enumerate(nodes):
        if not in_campus(point[0], point[1]):
            report.check(False, f"routing node {index} at {point} is off campus")
            break

    for a, b, kind in edges:
        if not (0 <= a < len(nodes) and 0 <= b < len(nodes)):
            report.check(
                False,
                f"routing edge {[a, b, kind]} references a node that does not exist",
            )
            break
        if a == b:
            report.check(False, f"routing edge {[a, b, kind]} is a self-loop")
            break
        if kind not in declared:
            report.check(False, f"routing edge type {kind} is not in edgeTypes")
            break

    known = {record["id"] for record in records}
    for place_id, indices in entrances.items():
        report.check(
            place_id in known,
            f"routing entrance {place_id!r} matches no place in map.json",
        )
        report.check(
            all(0 <= i < len(nodes) for i in indices),
            f"routing entrance {place_id!r} points at a node that does not exist",
        )

    # One component, or a destination in a detached piece is unroutable — and
    # looks exactly like "there is no path", which is the failure that would
    # reach a user.
    neighbours: dict[int, set[int]] = {}
    for a, b, _ in edges:
        neighbours.setdefault(a, set()).add(b)
        neighbours.setdefault(b, set()).add(a)
    reached: set[int] = set()
    if nodes:
        stack = [0]
        while stack:
            current = stack.pop()
            if current in reached:
                continue
            reached.add(current)
            stack.extend(neighbours.get(current, set()) - reached)
    report.check(
        len(reached) == len(nodes),
        f"routing graph is not connected: {len(nodes) - len(reached)} of "
        f"{len(nodes)} nodes cannot be reached from node 0",
    )

    buildings = [r for r in records if "building" in r["categories"]]
    without = sorted(r["id"] for r in buildings if r["id"] not in entrances)
    if without:
        report.note(
            f"{len(without)} of {len(buildings)} buildings have no surveyed "
            f"entrance and will be routed to at their nearest node: "
            f"{', '.join(without)}"
        )


def verify_reproducible(report: Report) -> None:
    """The build must be a pure function of data/ and overrides.yaml.

    If it is not, the scheduled run commits a diff on every pass and the change
    log stops meaning anything.
    """
    before = {name: (ROOT / name).read_text() for name in ("map.json", "map.geojson")}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    if not report.check(
        result.returncode == 0, f"build.py failed on re-run: {result.stderr}"
    ):
        return
    for name, text in before.items():
        report.check(
            (ROOT / name).read_text() == text,
            f"{name} changed when its build script was run again — the build is not deterministic",
        )


def main() -> int:
    report = Report()

    verify_raw(report)
    records = verify_map_json(report)
    verify_map_geojson(report, records)
    verify_overrides(report, records)
    verify_routing(report, records)
    verify_reproducible(report)

    places = len(records)
    footprints = sum(1 for record in records if record.get("outline"))
    print(f"{len(slugs())} layers, {len(SOURCES)} sources", file=sys.stderr)
    print(f"{places} places, {footprints} with a footprint", file=sys.stderr)

    for note in report.notes:
        print(f"\nnote: {note}", file=sys.stderr)

    if report.failures:
        print(f"\n{len(report.failures)} check(s) failed:", file=sys.stderr)
        for failure in report.failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("\nall checks passed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
