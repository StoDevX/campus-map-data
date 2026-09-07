#!/usr/bin/env python3
"""Pull every declared ArcGIS layer into `data/*.geojson`.

These files are the archival record: one file per layer, source field names
preserved, no interpretation applied. `build.py` reads them to produce
`map.json` and `map.geojson`; anything that wants the college's data as the
college publishes it should read these instead.

The output is committed, so **its diff is the change log for campus data**. That
makes determinism the whole design constraint, and three things get normalised
to protect it:

- **Volatile fields are dropped.** `OBJECTID`, `FID` and `GlobalID` are database
  identity, not campus data, and they renumber wholesale when a layer is
  republished. `Shape__Area` and friends are recomputed server-side. Keeping any
  of them would mean a rewrite of the whole file on a republish that changed
  nothing anyone can see.

- **Feature order is imposed.** ArcGIS returns features in whatever order it
  likes. They are sorted by name, then by a hash of their geometry, so a stable
  feature keeps a stable position and a real edit shows up as a small diff.

- **Coordinates are rounded** to 7 decimals, which is about a centimetre.

Deliberately absent: any timestamp. A "scraped at" field would make every
scheduled run a commit, which is precisely the noise this is trying to avoid.
When the data last changed is the commit date; when it was last *checked* is the
workflow run history.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import arcgis
import geometry
from sources import DASHBOARD_ITEM, SOURCES, WEBMAP_ITEM, Source

DATA = Path(__file__).resolve().parent.parent / "data"

# Database bookkeeping and server-computed measurements. Neither describes the
# campus, and both churn.
VOLATILE_FIELDS = frozenset(
    {
        "OBJECTID",
        "OBJECTID_1",
        "FID",
        "GlobalID",
        "Shape__Area",
        "Shape__Length",
        "Shape_Area",
        "Shape_Leng",
        "Shape_Le_1",
        "SHAPE_Leng",
        "SHAPE_Area",
    }
)

# Which layer a feature came from. The points-of-interest file merges seven
# layers, and build.py needs to know which one a feature belongs to in order to
# categorise it. The underscore marks it as ours rather than the college's.
LAYER_PROPERTY = "_layer"


def clean_properties(properties: dict | None) -> dict:
    return {
        key: value
        for key, value in (properties or {}).items()
        if key not in VOLATILE_FIELDS
    }


def sort_key(feature: dict) -> tuple[str, str]:
    """Name first so the file reads sensibly, geometry hash to break ties.

    Two unnamed parking polygons are distinguishable only by where they are, and
    a hash of the geometry is both stable across runs and total.
    """
    properties = feature.get("properties") or {}
    name = ""
    for field in ("Name", "NAME"):
        value = properties.get(field)
        if isinstance(value, str) and value.strip():
            name = value.strip().casefold()
            break
    digest = hashlib.sha256(
        json.dumps(feature.get("geometry"), sort_keys=True).encode()
    ).hexdigest()
    return (name, digest)


def fetch(source: Source) -> list[dict]:
    features = []
    for feature in arcgis.query_features(source.url):
        properties = clean_properties(feature.get("properties"))
        properties[LAYER_PROPERTY] = source.title
        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": geometry.round_coords(feature.get("geometry")),
            }
        )
    return features


def write_geojson(path: Path, features: list[dict], name: str) -> None:
    collection = {
        "type": "FeatureCollection",
        # A GeoJSON foreign member (RFC 7946 §6.1) and the convention GDAL and
        # friends already use for a layer name. Readers that do not know it
        # ignore it.
        "name": name,
        "features": features,
    }
    path.write_text(json.dumps(collection, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    DATA.mkdir(exist_ok=True)

    by_slug: dict[str, list[dict]] = {}
    manifest: list[dict] = []

    for source in SOURCES:
        features = fetch(source)
        by_slug.setdefault(source.slug, []).extend(features)
        manifest.append(
            {
                "slug": source.slug,
                "title": source.title,
                "url": source.url,
                "role": source.role,
                "features": len(features),
            }
        )
        print(f"{source.title}: {len(features)} features", file=sys.stderr)

    for slug, features in by_slug.items():
        features.sort(key=sort_key)
        write_geojson(DATA / f"{slug}.geojson", features, slug)

    (DATA / "sources.json").write_text(
        json.dumps(
            {
                "dashboard": f"https://stolaf.maps.arcgis.com/apps/dashboards/{DASHBOARD_ITEM}",
                "webmap": f"https://www.arcgis.com/home/item.html?id={WEBMAP_ITEM}",
                "layers": manifest,
            },
            indent=2,
        )
        + "\n"
    )

    total = sum(len(features) for features in by_slug.values())
    print(f"\n{total} features across {len(by_slug)} files", file=sys.stderr)

    # An empty layer is almost certainly a service that moved rather than a
    # campus that lost its walkways, and it would silently blank a committed
    # file. Fail instead.
    empty = [entry["title"] for entry in manifest if entry["features"] == 0]
    if empty:
        print(f"\nERROR: no features returned by: {', '.join(empty)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
