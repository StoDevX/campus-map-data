#!/usr/bin/env python3
"""Turn the scraped layers into `map.json` and `map.geojson`.

The output shape is Carleton's, deliberately: `carls-app/map-data` publishes
these two files, ccc-server serves the GeoJSON at
`carleton.api.frogpond.tech/v1/map/geojson`, and AAO reads it. Matching the
shape means a St. Olaf endpoint is a configuration change rather than a second
code path. Every key Carleton emits is emitted here, even where St. Olaf has
nothing to put in it, so a consumer never has to test for a missing key.

Three properties are added beyond Carleton's set — `abbreviation`, `type` and
`links`. Extra keys are additive and safe for existing consumers, and dropping
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


def assign_ids(places: list[dict], overrides: dict) -> None:
    """Give every place a stable, unique id.

    The default is the slugified name, prefixed by its layer where a bare slug
    would collide across layers — the parking lot beside Rand Hall is called
    "Rand" too. `overrides.yaml`'s `ids:` map overrides it by source name.

    Several places genuinely share a name: three separate polygons are all
    called "Porter", and the accessible-parking layer is 26 points with no name
    at all. Those are numbered — and numbered **from 1**, so no arbitrary member
    of the group gets to be the unsuffixed one. `verify.py` reports them,
    because a numbered id usually means the source data wants an override.
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
        },
    }


def main() -> int:
    overrides = yaml.safe_load((ROOT / "overrides.yaml").read_text()) or {}
    places, dropped = read_places(source_index())

    assign_ids(places, overrides)
    places = apply_overrides(places, overrides)
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
