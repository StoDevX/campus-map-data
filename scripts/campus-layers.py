#!/usr/bin/env python3
"""Split map.geojson into the three layers that get tiled.

`carls-app/map-tiles` tiles Carleton's data as two layers — footprints and
label anchors. St. Olaf's dataset does not fit that shape, and forcing it to
would produce a bad map:

    buildings  38          parking  72          athletics  8    poi  10

**More than half of the places are parking.** Putting 46 parking polygons into
a layer called `campus_buildings` would be a lie the style then has to work
around, and drawing 72 parking labels at the same zoom as the 38 building
labels makes the campus unreadable. So the polygons split by what they are, and
every anchor carries a `kind` the style can gate on zoom.

| Layer | Geometry | What |
| --- | --- | --- |
| `campus_buildings` | MultiPolygon | The 38 building footprints — what the app hit-tests |
| `campus_grounds` | MultiPolygon | Parking lots and athletic fields |
| `campus_labels` | Point | Every anchor, with `kind` and `hasFootprint` |
| `campus_paths` | MultiLineString | The college's walkways and Natural Lands trails |

`campus_paths` comes from `data/` rather than `map.geojson`, because walkways
are not *places* — they have no name, no id and no label — so `build.py` leaves
them out of the published dataset. They matter here anyway: this is a map people
walk around a campus with, and 23 km of the college's own path data was being
scraped and then dropped on the floor.

`buildingId` is on all three and is the source feature's `id` — the same
property name `map-tiles` uses, so AAO's selection code keys off it unchanged.

Two things about the source shape drive this, both inherited from Carleton's
schema (see `build.py`):

1. Each feature is a **`GeometryCollection`** holding the polygon(s) and a
   point. Neither tippecanoe nor MapLibre supports that — it is valid GeoJSON
   the style spec does not cover — and both fail *silently*, so it is unwrapped
   here.
2. A place with several polygons is merged into one `MultiPolygon` so it
   highlights and hit-tests as a single thing.
"""

import json
import os
import sys

# Which polygon layer a place belongs in, and what its label is gated on. First
# match wins, so a place categorised both `building` and `athletics` — Skoglund,
# Tostrud — is a building, which is how someone looking for it thinks of it.
KINDS = (
    ("building", "campus_buildings"),
    # Before `parking`, which these also are. They are 26 points that all share
    # the name "Accessible Parking", so the style needs to tell them apart from
    # named lots in order not to stamp the same label 26 times across campus.
    ("accessible-parking", None),
    ("parking", "campus_grounds"),
    ("athletics", "campus_grounds"),
    ("point-of-interest", None),
)


def classify(categories: list[str]) -> tuple[str, str | None]:
    for kind, layer in KINDS:
        if kind in categories:
            return kind, layer
    return "other", None


def properties(feature: dict, kind: str) -> dict:
    # Kept deliberately small: every property here is paid for in every tile.
    # The full record — prose, departments, floor plans — is what ccc-server
    # serves; the tiles carry only what a *map* needs.
    props = feature.get("properties") or {}
    return {
        # The app keys its selection highlight off this, so it has to survive
        # tiling on every layer that can be tapped or labelled.
        "buildingId": feature["id"],
        "name": props.get("name") or "",
        "kind": kind,
    }


def env_floor(name: str) -> int:
    return int(os.environ.get(name) or 0)


def polygons_of(geometry: dict) -> list:
    """Every polygon in a geometry, as a list of MultiPolygon-shaped parts."""
    kind = geometry.get("type")
    if kind == "Polygon":
        return [geometry["coordinates"]]
    if kind == "MultiPolygon":
        return list(geometry["coordinates"])
    return []


# Paths come from these scraped layers, with the `kind` each contributes. The
# college's walkway layer is 138 lines and about as complete as the pedestrian
# graph in StoDevX/ole-compass (9.7 km of sidewalk); the trails are the Natural
# Lands, which are most of the campus by area.
PATH_SOURCES = (
    ("walkways.geojson", "walkway"),
    ("natural-lands-trails.geojson", "trail"),
)


def linestrings_of(geometry: dict) -> list:
    """Every line in a geometry, as a list of MultiLineString-shaped parts."""
    kind = geometry.get("type")
    if kind == "LineString":
        return [geometry["coordinates"]]
    if kind == "MultiLineString":
        return list(geometry["coordinates"])
    return []


def paths(datadir: str) -> list[dict]:
    features = []
    for filename, kind in PATH_SOURCES:
        path = os.path.join(datadir, filename)
        if not os.path.exists(path):
            print(f"  note: {path} missing, no {kind} paths", file=sys.stderr)
            continue
        with open(path) as f:
            collection = json.load(f)
        lines = [
            line
            for feature in collection.get("features") or []
            for line in linestrings_of(feature.get("geometry") or {})
        ]
        # One feature per kind rather than per source line. Nothing here is
        # individually addressable — no name, no id — and merging lets the
        # renderer treat the whole network as one thing.
        if lines:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "MultiLineString", "coordinates": lines},
                    "properties": {"kind": kind},
                }
            )
        print(f"  {kind:<8} {len(lines)} lines from {filename}")
    return features


def main(src: str, datadir: str, outdir: str) -> None:
    with open(src) as f:
        data = json.load(f)
    features = data.get("features") or []
    if not features:
        raise SystemExit(f"{src}: no features")

    collections: dict[str, list] = {
        "campus_buildings": [],
        "campus_grounds": [],
        "campus_labels": [],
        "campus_paths": [],
    }
    problems = []

    for feature in features:
        if not feature.get("id"):
            problems.append(
                f"feature with no id: {(feature.get('properties') or {}).get('name')!r}"
            )
            continue

        geometry = feature.get("geometry") or {}
        parts = (
            geometry.get("geometries")
            if geometry.get("type") == "GeometryCollection"
            else [geometry]
        ) or []

        rings = []
        points = []
        for part in parts:
            rings.extend(polygons_of(part))
            if part.get("type") == "Point":
                points.append(part["coordinates"])

        kind, layer = classify(
            (feature.get("properties") or {}).get("categories") or []
        )
        props = properties(feature, kind)

        if rings:
            if layer is None:
                problems.append(
                    f"{feature['id']}: has a footprint but no polygon layer"
                )
            else:
                collections[layer].append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "MultiPolygon", "coordinates": rings},
                        "properties": props,
                    }
                )

        if len(points) > 1:
            problems.append(
                f"{feature['id']}: {len(points)} label anchors, using the first"
            )
        if points:
            collections["campus_labels"].append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": points[0]},
                    "properties": {**props, "hasFootprint": bool(rings)},
                }
            )
        else:
            problems.append(f"{feature['id']}: no label anchor, will not be labelled")

    collections["campus_paths"] = paths(datadir)

    os.makedirs(outdir, exist_ok=True)
    for name, collection in collections.items():
        # Sorted so a rebuild of unchanged data produces an identical file.
        collection.sort(
            key=lambda f: f["properties"].get("buildingId") or f["properties"]["kind"]
        )
        with open(os.path.join(outdir, f"{name}.geojson"), "w") as out:
            json.dump({"type": "FeatureCollection", "features": collection}, out)

    print(f"  {len(features)} source features")
    for name, collection in collections.items():
        if name == "campus_paths":
            continue  # already reported above, per source
        extra = ""
        if name == "campus_labels":
            without = sum(1 for f in collection if not f["properties"]["hasFootprint"])
            extra = f" ({without} without a footprint)"
        else:
            multi = sum(1 for f in collection if len(f["geometry"]["coordinates"]) > 1)
            extra = f" ({multi} merged from several polygons)"
        print(f"  {name:<22} {len(collection)}{extra}")
    for problem in problems:
        print(f"  note: {problem}")

    # Every source feature must come through on at least one layer. A silent
    # shortfall here would look identical to a tiling problem later.
    covered = {
        f["properties"]["buildingId"]
        for collection in collections.values()
        for f in collection
        if "buildingId" in f["properties"]
    }
    missing = {f["id"] for f in features if f.get("id")} - covered
    if missing:
        raise SystemExit(
            f"  {len(missing)} source features reached no layer: {sorted(missing)[:10]}"
        )

    # The check above catches features lost *between* the source and a layer.
    # This one catches a source that arrived short in the first place, which
    # that check cannot see: a map.geojson holding a fraction of the places
    # passes every assertion here, since nothing was dropped — there was simply
    # less to drop.
    short = [
        f"{name}: {len(collections[name])} features, floor is {floor}"
        for name, floor in (
            ("campus_buildings", env_floor("MIN_CAMPUS_BUILDINGS")),
            ("campus_grounds", env_floor("MIN_CAMPUS_GROUNDS")),
            ("campus_labels", env_floor("MIN_CAMPUS_LABELS")),
            ("campus_paths", env_floor("MIN_CAMPUS_PATHS")),
        )
        if len(collections[name]) < floor
    ]
    if short:
        raise SystemExit(
            "  campus data came up short — refusing to build:\n    "
            + "\n    ".join(short)
            + f"\n  Source was {src}, {len(features)} features."
            "\n  If the college really did remove this many, lower the floor in build-tiles.sh."
        )


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: campus-layers.py <map.geojson> <data-dir> <outdir>")
    main(sys.argv[1], sys.argv[2], sys.argv[3])
