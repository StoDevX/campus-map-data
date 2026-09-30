#!/usr/bin/env python3
"""Turn the scraped layers into `map.json` and `map.geojson`.

The output shape is Carleton's, deliberately: `carls-app/map-data` publishes
these two files, ccc-server serves the GeoJSON at
`carleton.api.frogpond.tech/v1/map/geojson`, and AAO reads it. Matching the
shape means a St. Olaf endpoint is a configuration change rather than a second
code path. Every key Carleton emits is emitted here, even where St. Olaf has
nothing to put in it, so a consumer never has to test for a missing key.

Seven properties are added beyond Carleton's set — `abbreviation`, `type`,
`links`, `parent`, `length`, `rules` and `walk`. Extra keys are additive and safe for existing consumers, and dropping
St. Olaf's building abbreviations (`RNS`, `BMC`, `TOH`) to preserve an exact
field list would be throwing away the identifiers people on campus actually use.

## Watch the coordinate order

Carleton's two files disagree with each other, and this reproduces that
faithfully rather than fixing it, because consumers are written against it:

| Where | Order |
| --- | --- |
| `map.json` — `center`, `outline` | **latitude first** |
| `map.geojson` — all coordinates | longitude first (GeoJSON, RFC 7946) |
| `overrides.yaml` — `centerpoint` | longitude first |

## What comes from where

- **Geometry, name, type, prose** — the ArcGIS layers, via `data/*.geojson`.
- **Floors** — `data/residence-life.json`, joined on the residence-life URL a
  building's own prose links to.
- **Departments** — the links inside that prose. St. Olaf's data does not
  separate academic departments from administrative offices the way Carleton's
  does, so they all land in `departments` and `offices` stays empty rather than
  being filled by guesswork.
- **Label anchors** — computed (see `geometry.py`), correctable in
  `overrides.yaml`.
- **Address, photo, accessibility** — nothing. No St. Olaf source publishes
  them per building; see `enrich.py` for why the address that looks scrapeable
  is not the building's.
"""

from __future__ import annotations

import html
import json
import math
import re
import sys
from pathlib import Path

import geometry
import yaml
from sources import SOURCES, Source

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

LAYER_PROPERTY = "_layer"

ANCHOR = re.compile(r"(?is)<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>")
RESIDENCE_PAGE = re.compile(
    r"https://wp\.stolaf\.edu/residencelife/[a-z0-9-]+/?", re.IGNORECASE
)
# A department or programme page: wp.stolaf.edu/<something>. News articles are
# stories about a building, not things housed in it.
DEPARTMENT_PAGE = re.compile(
    r"^https://wp\.stolaf\.edu/(?!news/|residencelife/)", re.IGNORECASE
)

# ArcGIS `Type` values, mapped to category slugs. A type absent from this map
# still becomes a category — slugified — so a new type on campus shows up in the
# data rather than vanishing; the map is here to split the compound values and
# to keep the common ones stable if the college rewords them.
TYPE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Residence Hall": ("residence-hall", "housing"),
    "Administrative & Academic": ("administrative", "academic"),
    "Administrative": ("administrative",),
    "Athletics": ("athletics",),
    "Student Center, Visitor Center, & Dining": (
        "student-center",
        "visitor-center",
        "dining",
    ),
    "Athletics Field": ("athletics", "field"),
    "General Vistor Parking": ("visitor-parking",),
    "Campus Parking": ("campus-parking",),
}

# Places whose id would otherwise collide across layers — the parking lot beside
# Rand Hall is called "Rand" too — get their layer's prefix. Buildings and
# points of interest keep bare ids, which is what a consumer is most likely to
# hardcode.
ID_PREFIXES = {
    "parking-lots": "lot",
    "accessible-parking": "accessible-parking",
    "athletic-fields": "field",
    "water": "pond",
    "natural-lands-trails": "trail",
}


def clean(value) -> str | None:
    """Trim, and treat whitespace-only as absent.

    The parking layer uses `' '` where it means null — 20 of its 52 rows have a
    single-space `Name` — so an untrimmed read produces places called " ".
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def plain_text(markup: str | None) -> str | None:
    if not markup:
        return None
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>", " ", markup)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip() or None


def slugify(value: str) -> str:
    """An id in Carleton's style: `oldmain`, `musichall`, `larsonhall`.

    Runs the words together rather than hyphenating them, because that is the
    convention `carls-app/map-data` established and ids are the thing consumers
    hardcode.
    """
    # Leading parentheticals are first names the college prints but nobody says:
    # "(Agnes) Larson Hall" is Larson Hall.
    value = re.sub(r"^\s*\([^)]*\)\s*", "", value)
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def hyphenate(value: str) -> str:
    """A category slug: `residence-hall`, `admissions-parking`.

    Categories are read by people writing filters, so they keep their word
    boundaries. Ids do not, which is why these are two functions and not one.
    """
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def links_in(markup: str | None) -> tuple[list[dict], list[dict]]:
    """Split a building's prose links into departments and everything else."""
    departments: list[dict] = []
    other: list[dict] = []
    seen: set[str] = set()

    for match in ANCHOR.finditer(markup or ""):
        href = html.unescape(match.group(1))
        label = plain_text(match.group(2))
        if (
            not label
            or href in seen
            or RESIDENCE_PAGE.fullmatch(href.rstrip("/") + "/")
        ):
            continue
        seen.add(href)
        (departments if DEPARTMENT_PAGE.match(href) else other).append(
            {"href": href, "label": label}
        )

    return departments, other


def source_index() -> dict[str, Source]:
    return {source.title: source for source in SOURCES}


def categories_for(source, properties: dict) -> list[str]:
    found = list(source.categories)
    kind = clean(properties.get("Type"))
    if kind:
        found.extend(TYPE_CATEGORIES.get(kind) or (hyphenate(kind),))
    # Order-preserving dedupe: the first mention wins, so the layer's own
    # categories stay in front and `categories[0]` is a stable primary.
    return list(dict.fromkeys(category for category in found if category))


def name_for(source, properties: dict) -> str | None:
    for field in source.name_fields:
        name = clean(properties.get(field))
        if name:
            return name
    # Parking lots are often unnamed but numbered: "Lot N" is a usable name.
    lot = clean(properties.get("Lot"))
    if lot:
        return lot
    return source.fallback_name


def read_places(sources_by_title: dict) -> tuple[list[dict], list[str]]:
    """Every feature from a `place` layer, as a draft record. Plus what was dropped."""
    places: list[dict] = []
    dropped: list[str] = []

    for slug in dict.fromkeys(
        source.slug for source in SOURCES if source.role == "place"
    ):
        collection = json.loads((DATA / f"{slug}.geojson").read_text())
        for feature in collection["features"]:
            properties = dict(feature["properties"])
            source = sources_by_title[properties.pop(LAYER_PROPERTY)]
            name = name_for(source, properties)
            if not name:
                # An unnamed polygon cannot be labelled, searched for, or linked
                # to. The geometry is still published in data/<slug>.geojson;
                # it just is not a *place*.
                dropped.append(slug)
                continue

            departments, other = links_in(properties.get("Information"))
            places.append(
                {
                    "slug": slug,
                    "name": name,
                    "abbreviation": clean(properties.get("ABB")),
                    "type": clean(properties.get("Type")),
                    "categories": categories_for(source, properties),
                    "description": plain_text(
                        properties.get("Information") or properties.get("Details")
                    ),
                    "departments": departments,
                    "links": other
                    + [
                        {"href": url, "label": label}
                        for label, url in (
                            ("Website", clean(properties.get("Website"))),
                        )
                        if url
                    ],
                    "contact": clean(properties.get("Contact")),
                    "geometry": feature["geometry"],
                    "information": properties.get("Information") or "",
                }
            )

    return places, dropped


# How far a piece may lie from the rest of its lot and still be merged into it.
# The widest gap between two pieces of one lot today is Below Regents Hall's,
# about 20 m.
LOT_PIECE_REACH_M = 50

# What pieces of one lot are expected to agree on. The description is compared
# after `plain_text`, so pieces that differ only in whitespace agree.
LOT_PIECE_FIELDS = ("description", "type", "categories", "links")


def merge_split_lots(places: list[dict]) -> list[dict]:
    """One place per parking lot, however many pieces the college draws it in.

    Six lots come as two or three polygons under one name -- Porter is three --
    with the same description on each piece. As separate places they list as
    several lots of one name and number their ids, and nothing says which
    piece is which, because nothing tells them apart. Merged, each is one
    MultiPolygon, as the two buildings drawn in pieces already are.

    A piece more than `LOT_PIECE_REACH_M` from the rest is another lot that
    shares the name, and stays apart -- where verify.py then reports the two
    names. A piece that disagrees with the first on anything but its shape is
    merged, keeping the first's fields, and reported.
    """
    kept: list[dict] = []
    by_name: dict[str, dict] = {}
    for place in places:
        if place["slug"] != "parking-lots":
            kept.append(place)
            continue
        first = by_name.get(place["name"])
        if first is None or not near(place["geometry"], first["geometry"]):
            by_name.setdefault(place["name"], place)
            kept.append(place)
            continue
        differ = [f for f in LOT_PIECE_FIELDS if place.get(f) != first.get(f)]
        if differ:
            print(
                f"  ! {place['name']!r}: pieces disagree on {', '.join(differ)}; "
                "keeping the first's",
                file=sys.stderr,
            )
        first["geometry"] = {
            "type": "MultiPolygon",
            "coordinates": geometry.polygons(first["geometry"])
            + geometry.polygons(place["geometry"]),
        }
    return kept


def near(piece: dict, lot: dict) -> bool:
    """Whether any corner of `piece` lies within `LOT_PIECE_REACH_M` of `lot`."""
    return any(
        geometry.distance_m(corner, lot) <= LOT_PIECE_REACH_M
        for corner in geometry.positions(piece)
    )


# How far from a lot or building an accessible spot may sit and still be named
# for it. Twenty-three of the 26 sit inside a lot, two within 10 m of one, and
# one 19 m from New Hall, which has no lot of its own.
ACCESSIBLE_REACH_M = 25


def name_accessible_spots(places: list[dict]) -> None:
    """Name each accessible spot for the lot it is in, or failing that the lot
    or building nearest it, and make that its parent.

    The layer is 26 points with no name at all, so all 26 read "Accessible
    Parking". Derived rather than hand-written, unlike the rooms' parents under
    `changes`: a spot the college adds or moves is named on the next scrape,
    and one out of reach of everything is left alone for verify.py to report.
    """
    lots = [place for place in places if place["slug"] == "parking-lots"]
    buildings = [place for place in places if place["slug"] == "buildings"]
    for spot in places:
        if spot["slug"] != "accessible-parking":
            continue
        # A parent set by hand in overrides.yaml knows better than the geometry.
        if spot.get("parent"):
            continue
        point = geometry.positions(spot["geometry"])[0]
        for candidates in (lots, buildings):
            # Ties go to the lower id, so a rebuild names a spot the same way.
            distance, _, nearest = min(
                (
                    (geometry.distance_m(point, place["geometry"]), place["id"], place)
                    for place in candidates
                ),
                key=lambda entry: entry[:2],
                default=(math.inf, "", None),
            )
            if nearest is not None and distance <= ACCESSIBLE_REACH_M:
                spot["parent"] = nearest["id"]
                spot["name"] = f"Accessible Parking, {nearest['name']}"
                break


# How far an assembled trail's length may move from the `miles` overrides.yaml
# records before the build refuses it: further means the college re-cut its
# segments, and the FIDs no longer mean what the mapping says.
TRAIL_MILES_TOLERANCE = 0.05
# How near a cut must be to a vertex, and a trail's parts to each other.
TRAIL_REACH_M = 5
METRES_PER_MILE = 1609.344


def touches(a: list, b: list, reach: float) -> bool:
    """Whether a vertex of either line lies within `reach` metres of the other."""
    line_a = {"type": "LineString", "coordinates": a}
    line_b = {"type": "LineString", "coordinates": b}
    return any(geometry.distance_m(v, line_b) <= reach for v in a) or any(
        geometry.distance_m(v, line_a) <= reach for v in b
    )


def assemble_trails(segments: list[dict], spec: dict) -> list[dict]:
    """The trails `overrides.yaml` builds from the college's segments.

    Each segment is named by FID, whole, or as the part of it before (`until`)
    or after (`from`) the vertex nearest a point, in the direction the college
    drew the segment -- the one cut, where a
    connector the map draws as part of one trail runs in a segment of another.
    Both parts keep that vertex, so the two trails meet.
    """
    by_fid: dict[int, dict] = {}
    held_twice = set()
    for feature in segments:
        fid = feature["properties"]["FID"]
        if fid in by_fid:
            held_twice.add(fid)
        by_fid[fid] = feature
    trails = []
    for entry in spec.get("trails") or []:

        def stale(fid, why, entry=entry):
            return SystemExit(
                f"  {entry['name']}: FID {fid} {why}; "
                "the college may have republished the layer -- redo `trails:`"
            )

        lines = []
        fids = []
        for part in entry["segments"]:
            fid = part if isinstance(part, int) else part["fid"]
            if fid not in by_fid:
                raise stale(fid, "is not in the segments layer")
            if fid in held_twice:
                raise stale(fid, "is in the segments layer twice")
            if by_fid[fid]["geometry"]["type"] != "LineString":
                raise stale(fid, "is drawn in pieces, not as one line")
            line = by_fid[fid]["geometry"]["coordinates"]
            if isinstance(part, dict) and ("until" in part or "from" in part):
                point = part.get("until") or part.get("from")
                cut = min(
                    range(len(line)),
                    key=lambda i, line=line, point=point: geometry.distance_m(
                        point, {"type": "Point", "coordinates": line[i]}
                    ),
                )
                if (
                    geometry.distance_m(
                        point, {"type": "Point", "coordinates": line[cut]}
                    )
                    > TRAIL_REACH_M
                ):
                    raise stale(
                        fid, f"has no vertex within {TRAIL_REACH_M} m of its cut"
                    )
                line = line[: cut + 1] if "until" in part else line[cut:]
            lines.append(line)
            fids.append(fid)
        # A wrong FID can match the trail's length by chance; it cannot also
        # meet the rest of the trail. Parts meet end to end or at a T, where
        # one's end lies partway along the other.
        for index, line in enumerate(lines):
            others = lines[:index] + lines[index + 1 :]
            if others and not any(
                touches(line, other, TRAIL_REACH_M) for other in others
            ):
                raise stale(fids[index], "touches no other part of the trail")
        shape = {"type": "MultiLineString", "coordinates": lines}
        miles = geometry.length_m(shape) / METRES_PER_MILE
        if abs(miles - entry["miles"]) > TRAIL_MILES_TOLERANCE:
            raise SystemExit(
                f"  {entry['name']}: assembles to {miles:.2f} mi, not "
                f"{entry['miles']} -- the college may have re-cut its segments"
            )
        trails.append(
            {
                "slug": "natural-lands-trails",
                "name": entry["name"],
                "abbreviation": None,
                "type": None,
                "categories": ["outdoors", "trail"],
                "description": None,
                "departments": [],
                "links": [],
                "contact": None,
                "geometry": shape,
                "information": "",
            }
        )
    return trails


RULES_LINK_LABEL = "Natural Lands rules"


def apply_rules(places: list[dict], spec: dict) -> None:
    """The rules `overrides.yaml` gives the Natural Lands' ponds and trails,
    in its own words, and a link to the college's page. Every other place gets
    none, so a consumer reads one key rather than testing for its absence."""
    categories = set(spec.get("categories") or [])
    link = {"label": RULES_LINK_LABEL, "href": spec.get("href")}
    for place in places:
        if not categories & set(place.get("categories") or []):
            place["rules"] = []
            continue
        swaps = (spec.get("replace") or {}).get(place["id"]) or {}
        place["rules"] = [swaps.get(rule, rule) for rule in spec.get("shared") or []]
        links = place.setdefault("links", [])
        if link not in links:
            links.append(link)


WALK_LINK_LABEL = "Wellness Walk guide"


def walk_places(places: list[dict], spec: dict) -> list[dict]:
    """The walks along part of a trail, as places of their own.

    Named by trail name, not id: these are made before ids are given. The
    part is one line of the trail's MultiLineString, in the order the college
    drew them.
    """
    by_name = {
        place["name"]: place
        for place in places
        if place["slug"] == "natural-lands-trails"
    }
    walks = []
    for entry in spec.get("walks") or []:
        part = entry.get("part")
        if not part:
            continue
        trail = by_name.get(part["trail"])
        lines = (trail or {}).get("geometry", {}).get("coordinates") or []
        if (
            trail is None
            or trail["geometry"]["type"] != "MultiLineString"
            or not 0 <= part["index"] < len(lines)
        ):
            raise SystemExit(
                f"  {entry['name']}: no part {part['index']} of {part['trail']!r} "
                "-- did the college redraw the trail? See overrides.yaml `walks:`"
            )
        walks.append(
            {
                "slug": "natural-lands-trails",
                "name": entry["name"],
                "abbreviation": None,
                "type": None,
                "categories": ["outdoors", "trail", "wellness-walk"],
                "description": None,
                "departments": [],
                "links": [],
                "contact": None,
                "geometry": {"type": "LineString", "coordinates": lines[part["index"]]},
                "information": "",
            }
        )
    return walks


def part_walk_ids_free(places: list[dict], walks: list[dict]) -> None:
    """The part-walks take ids of their own, given apart from the rest: a trail
    the college names like a walk would otherwise share its id."""
    taken = {place["id"] for place in places}
    for walk in walks:
        if walk["id"] in taken:
            raise SystemExit(
                f"  walks: {walk['id']!r} is already a place's id -- give the "
                "walk another name in overrides.yaml `walks:`"
            )


def apply_walks(places: list[dict], spec: dict) -> None:
    """Every walk's time, accessibility and guide, on the place it follows.

    Every other place gets `walk: None`, so a consumer reads one key rather
    than testing for its absence.
    """
    by_id = {place["id"]: place for place in places}
    by_name = {place["name"]: place for place in places}
    for place in places:
        place["walk"] = None
    for entry in spec.get("walks") or []:
        place = (
            by_id.get(entry["trail"])
            if "trail" in entry
            else by_name.get(entry["name"])
        )
        if place is None:
            raise SystemExit(
                f"  walks: no place {entry.get('trail') or entry.get('name')!r} "
                "-- was it renamed? See overrides.yaml `walks:`"
            )
        if "trail" not in place["categories"]:
            raise SystemExit(
                f"  walks: {place['id']!r} is not a trail -- a typo in "
                "overrides.yaml `walks:`?"
            )
        place["walk"] = {
            "minutes": entry["minutes"],
            "accessibility": entry["accessibility"],
        }
        if "wellness-walk" not in place["categories"]:
            place["categories"].append("wellness-walk")
        link = {"label": WALK_LINK_LABEL, "href": entry["pdf"]}
        links = place.setdefault("links", [])
        if link not in links:
            links.append(link)


def scraped_places(overrides: dict) -> tuple[list[dict], list[str]]:
    """The places as scraped, merged and given ids -- before overrides touch
    them. What an `overrides.yaml` id has to match. Plus what was dropped."""
    places, dropped = read_places(source_index())
    trails = overrides.get("trails") or {}
    superseded = set(trails.get("supersedes") or [])
    places = [
        place
        for place in places
        if not (place["slug"] == "natural-lands-trails" and place["name"] in superseded)
    ]
    segments = json.loads((DATA / "natural-lands-segments.geojson").read_text())
    places += assemble_trails(segments["features"], trails)
    places = merge_split_lots(places)
    assign_ids(places, overrides)
    # The walks along part of a trail, made from that trail and given ids of
    # their own: their names are unique, so the others' ids do not move.
    walks = walk_places(places, overrides.get("walks") or {})
    assign_ids(walks, overrides)
    part_walk_ids_free(places, walks)
    places += walks
    return places, dropped


def assign_ids(places: list[dict], overrides: dict) -> None:
    """Give every place a stable, unique id.

    The default is the slugified name, prefixed by its layer where a bare slug
    would collide across layers — the parking lot beside Rand Hall is called
    "Rand" too. `overrides.yaml`'s `ids:` map overrides it by source name.

    Several places genuinely share a name: the accessible-parking layer is 26
    points with no name at all, and are named for their lots only after ids
    are given. Those are numbered — and numbered **from 1**, so no arbitrary
    member of the group gets to be the unsuffixed one. `verify.py` reports ids
    numbered for a name still shared, because that usually means the source
    data wants an override.
    """
    # Scoped by layer, because a bare name is ambiguous across layers: the
    # parking lot beside Old Main is also called "Old Main", and a flat
    # name->id map silently gave both the same id.
    by_layer = overrides.get("ids") or {}

    def base_for(place: dict) -> str:
        explicit = (by_layer.get(place["slug"]) or {}).get(place["name"])
        if explicit:
            return explicit
        prefix = ID_PREFIXES.get(place["slug"])
        base = slugify(place["name"])
        # Skip a prefix the name already carries, so the fallback-named
        # accessible-parking points are `accessibleparking-1` rather than
        # `accessible-parking-accessibleparking-1`.
        if prefix and not base.startswith(slugify(prefix)):
            return f"{prefix}-{base}"
        return base

    ordered = sorted(places, key=lambda item: (item["slug"], item["name"]))
    for place in ordered:
        place["_base"] = base_for(place)

    counts: dict[str, int] = {}
    for place in ordered:
        counts[place["_base"]] = counts.get(place["_base"], 0) + 1

    used: dict[str, int] = {}
    for place in ordered:
        base = place.pop("_base")
        if counts[base] == 1:
            place["id"] = base
            continue
        used[base] = used.get(base, 0) + 1
        place["id"] = f"{base}-{used[base]}"


def apply_overrides(places: list[dict], overrides: dict) -> list[dict]:
    removals = {entry["id"] for entry in (overrides.get("removals") or [])}
    places = [place for place in places if place["id"] not in removals]

    by_id = {place["id"]: place for place in places}

    for change in overrides.get("changes") or []:
        place = by_id.get(change["id"])
        if place is None:
            # Loud, not silent: a change keyed to an id that no longer exists
            # means the source renamed something, and the override is now doing
            # nothing.
            print(
                f"  ! overrides.yaml: no place with id {change['id']!r}",
                file=sys.stderr,
            )
            continue
        for key, value in change.items():
            if key == "id":
                continue
            if key == "centerpoint":
                place["anchor"] = [float(value[0]), float(value[1])]
            else:
                place[key] = value

    for addition in overrides.get("additions") or []:
        places.append({**addition, "slug": addition.get("slug", "additions")})

    return places


def attach_floors(places: list[dict]) -> None:
    path = DATA / "residence-life.json"
    if not path.exists():
        print("  ! data/residence-life.json missing — no floor plans", file=sys.stderr)
        return
    pages = json.loads(path.read_text())

    for place in places:
        match = RESIDENCE_PAGE.search(place.get("information") or "")
        if not match:
            continue
        page = pages.get(match.group(0).rstrip("/") + "/")
        if page:
            place["floors"] = page["floors"]


def length_of(place: dict) -> int | None:
    """A trail's length in metres; None for a place with no line, or one that
    rounds to no metres at all."""
    return round(geometry.length_m(place.get("geometry"))) or None


def record(place: dict) -> dict:
    """One `map.json` record — Carleton's key set, latitude-first coordinates."""
    anchor = place.get("anchor") or geometry.label_anchor(place.get("geometry"))
    shapes = geometry.polygons(place.get("geometry"))
    # Carleton's `outline` is a single ring. Two St. Olaf buildings and one
    # athletic field are MultiPolygons, so this takes their largest part; the
    # complete geometry is preserved in map.geojson, which is what consumers
    # that care about footprints actually read.
    outline = None
    if shapes:
        largest = max(shapes, key=lambda rings: abs(geometry.ring_area(rings[0])))
        outline = [[point[1], point[0]] for point in largest[0]]

    return {
        "accessibility": place.get("accessibility", "unknown"),
        "address": place.get("address"),
        # Carleton's map.json uses an object-of-flags; its map.geojson uses a
        # list. Both are reproduced.
        "categories": dict.fromkeys(place["categories"], True),
        "center": [anchor[1], anchor[0]] if anchor else None,
        "departments": place.get("departments") or [],
        "description": place.get("description"),
        "floors": place.get("floors") or [],
        "id": place["id"],
        "name": place["name"],
        "offices": place.get("offices") or [],
        "outline": outline,
        "photo": place.get("photo"),
        # Beyond Carleton's set — see the module docstring.
        "abbreviation": place.get("abbreviation"),
        "type": place.get("type"),
        "links": place.get("links") or [],
        "parent": place.get("parent"),
        "length": length_of(place),
        "rules": place.get("rules") or [],
        "walk": place.get("walk"),
    }


def feature(place: dict) -> dict:
    """One `map.geojson` feature — GeometryCollection, longitude-first."""
    anchor = place.get("anchor") or geometry.label_anchor(place.get("geometry"))
    geometries = []
    source_geometry = place.get("geometry")
    if source_geometry and source_geometry.get("type") != "Point":
        geometries.append(source_geometry)
    if anchor:
        geometries.append(
            {"type": "Point", "coordinates": geometry.round_coords(anchor)}
        )

    return {
        "type": "Feature",
        "id": place["id"],
        "geometry": {"type": "GeometryCollection", "geometries": geometries},
        "properties": {
            "accessibility": place.get("accessibility", "unknown"),
            "address": place.get("address"),
            "categories": place["categories"],
            "departments": place.get("departments") or [],
            "description": place.get("description") or "",
            # Carleton flattens these to "label <href>" strings in its GeoJSON,
            # having kept objects in map.json. Reproduced as-is.
            "floors": [
                f"{entry['label']} <{entry['href']}>"
                for entry in place.get("floors") or []
            ],
            "name": place["name"],
            "nickname": place.get("abbreviation") or "",
            "offices": place.get("offices") or [],
            "abbreviation": place.get("abbreviation"),
            "type": place.get("type"),
            "links": place.get("links") or [],
            # The area a point-only place belongs to: the building a room or
            # counter sits inside (overrides.yaml), or the lot or building an
            # accessible spot serves (derived). Null otherwise. Emitted on
            # every feature rather than only where it applies, so a consumer
            # reads one key rather than testing for its absence.
            "parent": place.get("parent"),
            # A trail's length in metres, from its geometry; null otherwise.
            "length": length_of(place),
            # The Natural Lands rules, one sentence each; empty elsewhere.
            "rules": place.get("rules") or [],
            # The Wellness Walk along this place: its time and accessibility.
            "walk": place.get("walk"),
        },
    }


def main() -> int:
    overrides = yaml.safe_load((ROOT / "overrides.yaml").read_text()) or {}
    places, dropped = scraped_places(overrides)
    places = apply_overrides(places, overrides)
    name_accessible_spots(places)
    apply_rules(places, overrides.get("rules") or {})
    apply_walks(places, overrides.get("walks") or {})
    attach_floors(places)
    places.sort(key=lambda place: place["id"])

    (ROOT / "map.json").write_text(
        json.dumps([record(place) for place in places], indent=2, ensure_ascii=False)
        + "\n"
    )
    (ROOT / "map.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [feature(place) for place in places],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )

    with_footprint = sum(
        1 for place in places if geometry.polygons(place.get("geometry"))
    )
    print(
        f"{len(places)} places ({with_footprint} with a footprint), "
        f"{len(dropped)} unnamed features left as geometry only",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
