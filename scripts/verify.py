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
from collections import Counter
from pathlib import Path

import geometry
import yaml
from build import RULES_LINK_LABEL, WALK_LINK_LABEL, scraped_places
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

        # A layer that names its fields keeps them, volatile or not: the
        # segments layer is addressed by FID.
        kept = {
            field
            for source in SOURCES
            if source.slug == slug
            for field in source.fields or ()
        }
        leaked = {
            key
            for feature in features
            for key in (feature.get("properties") or {})
            if key in VOLATILE_FIELDS and key not in kept
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

    verify_parking(report, records)

    # Only where the names are shared too: the accessible spots keep numbered
    # ids but are named for their lots, so a list already tells them apart.
    shared = {
        n for n, count in Counter(r["name"] for r in records).items() if count > 1
    }
    numbered = sorted(
        {
            NUMBERED.sub("", record["id"])
            for record in records
            if NUMBERED.search(record["id"]) and record["name"] in shared
        }
    )
    if numbered:
        report.note(
            f"{len(numbered)} name(s) shared by several places, so their ids are "
            f"numbered: {', '.join(numbered)}. Give them real names in "
            f"overrides.yaml if the college has any."
        )
    return records


def verify_parking(report: Report, records: list[dict]) -> None:
    """Lots have one name each, and each accessible spot is named for its lot."""
    # build.py merges the scraped pieces of a lot, so a repeated lot name can
    # only come from a rename or an addition: two places sharing a lot's name
    # read as two lots in a list, and neither says which is which.
    lots = [
        record
        for record in records
        if "parking" in record["categories"]
        and "accessible-parking" not in record["categories"]
    ]
    for name, count in Counter(record["name"] for record in lots).items():
        report.check(
            count == 1,
            f"{count} parking lots share the name {name!r} — an overrides.yaml "
            f"rename or addition collides with it",
        )

    # Every accessible spot is named for the lot or building it serves, which
    # is its parent and has an area to frame: 26 places all called "Accessible
    # Parking" cannot be told apart in a list or a search. A name may add to
    # the parent's, which is how two spots in one lot are told apart by hand.
    by_id = {record["id"]: record for record in records}
    spots = [r for r in records if "accessible-parking" in r["categories"]]
    for record in spots:
        parent = by_id.get(record.get("parent") or "")
        if not report.check(
            parent is not None,
            f"{record['id']}: no lot or building within reach to name it after",
        ):
            continue
        report.check(
            bool(parent.get("outline")),
            f"{record['id']}: its parent {parent['id']} has no footprint to frame",
        )
        report.check(
            record["name"].startswith(f"Accessible Parking, {parent['name']}"),
            f"{record['id']}: named {record['name']!r}, not after its parent "
            f"{parent['name']!r}",
        )
    for name, count in Counter(record["name"] for record in spots).items():
        report.check(
            count == 1,
            f"{count} accessible spots are named {name!r} — tell them apart with a "
            f"name and parent under changes in overrides.yaml",
        )


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

        lines = [
            member
            for member in members
            if member["type"] in ("LineString", "MultiLineString")
        ]
        expected = round(sum(geometry.length_m(member) for member in lines)) or None
        report.check(
            feature["properties"].get("length") == expected,
            f"{feature['id']}: length {feature['properties'].get('length')} is not "
            f"its line's {expected}",
        )

        if points and lines and not areas:
            anchor = points[0]["coordinates"]
            # `label_anchor` takes a line's middle vertex, so the anchor is one
            # of the line's own positions, rounded as `build.py` rounds every
            # anchor while the line keeps the source's precision. A
            # `centerpoint` override is the only way it can leave the line.
            report.check(
                any(
                    geometry.round_coords(position) == anchor
                    for member in lines
                    for position in geometry.positions(member)
                ),
                f"{feature['id']}: label anchor {anchor} is not on its own line "
                f"— set a centerpoint for it in overrides.yaml",
            )


def verify_trails(report: Report, spec: dict, rows: list[dict]) -> None:
    """The assembled trails still replace what they were written to replace.

    A superseded row the college renames would come back as a place, drawn up
    to 147 m off the path; a row that takes an assembled trail's name would
    number both ids and lose the trail from anyone's Recents.
    """
    names = [((row.get("properties") or {}).get("NAME") or "").strip() for row in rows]
    superseded = set(spec.get("supersedes") or [])
    for name in sorted(superseded):
        report.check(
            name in names,
            f"overrides.yaml: trails supersedes {name!r}, which the trails layer "
            f"no longer has — did the source rename it?",
        )
    for entry in spec.get("trails") or []:
        report.check(
            entry["name"] not in names or entry["name"] in superseded,
            f"overrides.yaml: the trails layer now has its own {entry['name']!r}, "
            f"which the assembled trail of that name collides with",
        )


def verify_rules(report: Report, spec: dict, records: list[dict]) -> None:
    """Every rule swap still applies, the places in the rules' categories and
    no others carry them, and every place with rules links them.

    A swap keyed to a renamed id, or to a shared sentence since reworded, does
    nothing -- and Norway Valley's card would say bikes are allowed.
    """
    known = {record["id"] for record in records}
    shared = set(spec.get("shared") or [])
    for place, swaps in (spec.get("replace") or {}).items():
        report.check(
            place in known, f"overrides.yaml: rules replace for unknown id {place!r}"
        )
        for sentence in swaps:
            report.check(
                sentence in shared,
                f"overrides.yaml: rules replace for {place} swaps {sentence!r}, "
                f"which is not a shared rule",
            )
    categories = set(spec.get("categories") or [])
    report.check(bool(categories), "overrides.yaml: rules has no categories")
    for record in records:
        natural = categories & set(record["categories"])
        report.check(
            bool(record.get("rules")) == bool(natural),
            f"{record['id']}: rules "
            f"{'missing' if natural else 'on a place outside the Natural Lands'}",
        )
    link = {"label": RULES_LINK_LABEL, "href": spec.get("href")}
    for record in records:
        if record.get("rules"):
            report.check(
                (record.get("links") or []).count(link) == 1,
                f"{record['id']}: has rules but not exactly one link to them",
            )


# The guides' section of the walks page's snapshot, by its header: every walk
# has a guide, and Windmill Trail's has no heading.
GUIDES_SECTION = "# a.wp-block-file__button @href"


def snapshot_section(snapshot: str, header: str) -> set[str]:
    """The lines of one selector's section of a watch snapshot."""
    section: set[str] = set()
    inside = False
    for line in snapshot.splitlines():
        if line.startswith("# "):
            inside = line == header
        elif inside:
            section.add(line)
    return section


def verify_walks(
    report: Report, spec: dict, records: list[dict], snapshot: str | None
) -> None:
    """Every walk is whole on its place, and `walks:` links exactly the guides
    the page does. The snapshot is data/watches/wellness-walks.txt: once the
    watch's pull request records a changed walk, this fails until
    overrides.yaml matches it."""
    for record in records:
        walking = "wellness-walk" in record["categories"]
        has_walk = record.get("walk") is not None
        guides = [
            l for l in record.get("links") or [] if l.get("label") == WALK_LINK_LABEL
        ]
        report.check(
            walking == has_walk,
            f"{record['id']}: `walk` and the wellness-walk category disagree",
        )
        report.check(
            len(guides) == (1 if has_walk else 0),
            f"{record['id']}: has {len(guides)} Wellness Walk guide links",
        )
    if snapshot is None:
        return
    page = snapshot_section(snapshot, GUIDES_SECTION)
    listed = {entry["pdf"] for entry in spec.get("walks") or [] if "pdf" in entry}
    for missing in sorted(page - listed):
        report.check(
            False,
            f"overrides.yaml: the walks page links {missing}, which no walk in "
            f"`walks:` has -- update it to match data/watches/wellness-walks.txt",
        )
    for gone in sorted(listed - page):
        report.check(
            False,
            f"overrides.yaml: `walks:` has {gone}, which the walks page no longer "
            f"links -- update it to match data/watches/wellness-walks.txt",
        )


def verify_overrides(report: Report, records: list[dict]) -> None:
    overrides = yaml.safe_load((ROOT / "overrides.yaml").read_text()) or {}
    known = {record["id"] for record in records}
    layers = set(slugs())

    trail_rows = json.loads((DATA / "natural-lands-trails.geojson").read_text())
    verify_trails(report, overrides.get("trails") or {}, trail_rows["features"])
    verify_rules(report, overrides.get("rules") or {}, records)
    walks_snapshot = DATA / "watches" / "wellness-walks.txt"
    verify_walks(
        report,
        overrides.get("walks") or {},
        records,
        walks_snapshot.read_text() if walks_snapshot.exists() else None,
    )

    # A removal names a place by the id the build gives it, which the removed
    # place no longer has in map.json -- so rebuild the ids before removals to
    # see it. One that matches nothing means the source renamed the place, and
    # it is back in the dataset under its new id.
    removals = [entry["id"] for entry in overrides.get("removals") or []]
    if removals:
        scraped, _ = scraped_places(overrides)
        scraped_ids = {place["id"] for place in scraped}
        for removal in removals:
            report.check(
                removal in scraped_ids,
                f"overrides.yaml: removal of {removal!r} matches no scraped place "
                f"— did the source rename it?",
            )

    for change in overrides.get("changes") or []:
        report.check(
            change["id"] in known,
            f"overrides.yaml: changes entry for unknown id {change['id']!r}",
        )

    # A `parent` is only useful if it names a place that exists and has a
    # footprint to fall back to: the key exists so a consumer holding a
    # point-only place has an area to draw. A parent that is itself a point
    # leaves it exactly where it started, and one naming a dropped or renamed
    # id leaves it worse -- following a link to nothing.
    areas = {record["id"] for record in records if record.get("outline")}
    for change in overrides.get("changes") or []:
        parent = change.get("parent")
        if parent is None:
            continue
        if not report.check(
            parent in known,
            f"overrides.yaml: {change['id']} has parent {parent!r}, which is not a place",
        ):
            continue
        report.check(
            parent != change["id"],
            f"overrides.yaml: {change['id']} is its own parent",
        )
        report.check(
            parent in areas,
            f"overrides.yaml: {change['id']} has parent {parent!r}, which has no "
            f"footprint of its own -- it cannot stand in for one",
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
