"""Which ArcGIS layers this repo scrapes, and what each one becomes.

The list is the layer set behind **St. Olaf Campus Map (2026)** — ArcGIS Online
web map `48f433ddf4674d53b1754e307ffce1f4`, the map the college's public
dashboard (`039528a0ee874da7b5ef749c2393498d`) embeds. Taking the web map's own
operational layers, rather than every layer in every service, is what keeps this
list honest: it is by construction the data the college publishes as its campus
map.

That distinction matters because the services carry duplicates. Older copies of
the trails, walkways, roads and parking layers still sit in `St_Olaf_Campus_Data`
alongside the current ones in `StOlaf_Roads_2_WFL1` and
`St__Olaf_Parking_Lots_WFL1`, and the stale copies have lost their attributes —
`St_Olaf_Campus_Data/13` is the same 52 parking polygons as
`St__Olaf_Parking_Lots_WFL1/0` with every field but `Id` gone. The web map draws
the polygons from one and pops up the attributes from the other. We take the one
with the attributes.

Adding a layer is one `Source` entry. Nothing else in the pipeline enumerates
layers by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ORG = "https://services6.arcgis.com/5aTZOBLHgY2JnmmE/arcgis/rest/services"

# The two ArcGIS Online items this is all downstream of, recorded so the
# provenance survives someone reorganising the services.
DASHBOARD_ITEM = "039528a0ee874da7b5ef749c2393498d"
WEBMAP_ITEM = "48f433ddf4674d53b1754e307ffce1f4"


@dataclass(frozen=True)
class Source:
    """One scraped layer.

    `slug` names the file it lands in (`data/<slug>.geojson`). Several sources
    may share a slug, in which case their features are concatenated into one
    file — that is how the seven single-feature points-of-interest layers become
    one `points-of-interest.geojson`.
    """

    slug: str
    title: str
    url: str
    # What `build.py` should do with these features: "place" turns them into
    # records in map.json, "context" means geometry we publish but do not treat
    # as a named place (walkways, water, the campus boundary).
    role: str
    # Categories every feature from this layer gets, before per-feature
    # categories derived from its own `Type` field.
    categories: tuple[str, ...] = ()
    # Fallback name for layers whose features carry no name of their own. The
    # windmill's layer has literally no fields but `OBJECTID`; its identity
    # lives in the layer title.
    fallback_name: str | None = None
    # Property names to read a feature's name from, in order of preference.
    name_fields: tuple[str, ...] = ("Name", "NAME")
    notes: str = field(default="")


SOURCES: tuple[Source, ...] = (
    Source(
        slug="buildings",
        title="St. Olaf Campus Buildings",
        url=f"{ORG}/St_Olaf_Campus_Data/FeatureServer/10",
        role="place",
        categories=("building",),
        notes=(
            "The core of the dataset: 38 footprints with a name, an "
            "abbreviation, a type and a paragraph of HTML prose."
        ),
    ),
    Source(
        slug="parking-lots",
        title="St. Olaf - Parking Lots",
        url=f"{ORG}/St__Olaf_Parking_Lots_WFL1/FeatureServer/0",
        role="place",
        categories=("parking",),
        notes=(
            "Preferred over St_Olaf_Campus_Data/13, which is the same 52 "
            "polygons with Name, Type, Lot and Details dropped."
        ),
    ),
    Source(
        slug="athletic-fields",
        title="Athletic Fields",
        url=f"{ORG}/outdoorfields/FeatureServer/0",
        role="place",
        categories=("athletics",),
        notes="Several rows are unnamed; build.py drops those rather than emitting blanks.",
    ),
    Source(
        slug="accessible-parking",
        title="Accessible Parking",
        url=f"{ORG}/Accessible_Parking/FeatureServer/0",
        role="place",
        categories=("parking", "accessible-parking"),
        fallback_name="Accessible Parking",
        notes="Points only — the layer has no fields beyond FID and Id.",
    ),
    # The points of interest are seven separate single-purpose layers in one
    # service, each holding one to four features with the same four fields.
    # They are one concept, so they land in one file.
    *(
        Source(
            slug="points-of-interest",
            title=title,
            url=f"{ORG}/St__Olaf_Campus___Points_of_Interest_v3_WFL1/FeatureServer/{layer}",
            role="place",
            categories=("point-of-interest", *extra),
            fallback_name=fallback,
        )
        for layer, title, extra, fallback in (
            (0, "St. Olaf College Bookstore", ("bookstore",), None),
            (1, "Wind Chime Memorial", ("memorial",), None),
            (2, "Electric Vehicle Charger", ("ev-charging",), None),
            (3, "Buntrock Commons - Dining", ("dining",), None),
            (4, "Buntrock Commons - Visitor Desk", ("visitor-information",), None),
            (5, "Admissions Office", ("admissions",), None),
            # Layer 7 carries a single point and no attribute fields at all.
            (7, "Windmill", ("landmark",), "Windmill"),
        )
    ),
    Source(
        slug="campus-boundary",
        title="Campus Boundary",
        url=f"{ORG}/St_Olaf_Campus_Data/FeatureServer/14",
        role="context",
    ),
    Source(
        slug="water",
        title="Lakes",
        url=f"{ORG}/St_Olaf_Campus_Data/FeatureServer/11",
        role="context",
    ),
    Source(
        slug="walkways",
        title="Walkways",
        url=f"{ORG}/StOlaf_Roads_2_WFL1/FeatureServer/4",
        role="context",
    ),
    Source(
        slug="natural-lands-trails",
        title="Natural Lands Trails",
        url=f"{ORG}/StOlaf_Roads_2_WFL1/FeatureServer/3",
        role="context",
    ),
    Source(
        slug="campus-roads",
        title="Campus Roads",
        url=f"{ORG}/StOlaf_Roads_2_WFL1/FeatureServer/2",
        role="context",
    ),
    Source(
        slug="main-campus-roads",
        title="Main Campus Roads",
        url=f"{ORG}/StOlaf_Roads_2_WFL1/FeatureServer/7",
        role="context",
    ),
)


def slugs() -> list[str]:
    """Every distinct output file, in the order the sources declare them."""
    seen: dict[str, None] = {}
    for source in SOURCES:
        seen.setdefault(source.slug, None)
    return list(seen)
