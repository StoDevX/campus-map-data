# campus-map-data

St. Olaf College's campus map data, scraped from the college's own ArcGIS
services and published as GeoJSON. The St. Olaf counterpart to
[`carls-app/map-data`][map-data], and shaped to match it.

[map-data]: https://github.com/carls-app/map-data

## What is here

Two things, for two different readers.

| | What it is | Read it if |
| --- | --- | --- |
| `data/*.geojson` | One file per source layer, the college's field names untouched | You want St. Olaf's data as St. Olaf publishes it |
| `map.json`, `map.geojson` | 128 places in Carleton's schema | You are AAO, ccc-server, or anything already written against Carleton's |

Both are committed, and **the commit history is the change log for campus
data**: when a building is renamed, a lot is restriped, or a footprint is
corrected, that shows up here as a reviewable diff on the day the college makes
it. Everything about the pipeline is arranged to protect that — see
[Determinism](#determinism-is-the-whole-design).

## Where it comes from

Everything is downstream of one ArcGIS Online web map,
[**St. Olaf Campus Map (2026)**][webmap] (`48f433ddf4674d53b1754e307ffce1f4`),
which is what the college's [public dashboard][dashboard] embeds. Its layers are
shared publicly and advertise `Query`, so the scrape is unauthenticated and no
credential belongs in this repo.

[webmap]: https://www.arcgis.com/home/item.html?id=48f433ddf4674d53b1754e307ffce1f4
[dashboard]: https://stolaf.maps.arcgis.com/apps/dashboards/039528a0ee874da7b5ef749c2393498d

| Layer | Geometry | Features |
| --- | --- | ---: |
| `buildings.geojson` | Polygon, MultiPolygon | 38 |
| `parking-lots.geojson` | Polygon | 52 |
| `accessible-parking.geojson` | Point | 26 |
| `athletic-fields.geojson` | Polygon, MultiPolygon | 16 |
| `points-of-interest.geojson` | Point | 10 |
| `campus-boundary.geojson` | Polygon | 1 |
| `water.geojson` | Polygon | 27 |
| `walkways.geojson` | LineString | 138 |
| `natural-lands-trails.geojson` | LineString, MultiLineString | 23 |
| `campus-roads.geojson` | LineString, MultiLineString | 38 |
| `main-campus-roads.geojson` | MultiLineString | 1 |
| | | **370** |

Taking the *web map's* operational layers, rather than every layer in every
service, is what keeps that list honest: it is by construction the data the
college publishes as its campus map. It also avoids a trap. The services carry
stale duplicates — `St_Olaf_Campus_Data/13` is the same 52 parking polygons as
`St__Olaf_Parking_Lots_WFL1/0` with every field but `Id` dropped — and the web
map draws the polygons from one while popping up the attributes from the other.
We take the one with the attributes. `scripts/sources.py` is the whole registry;
adding a layer is one entry.

`points-of-interest.geojson` merges seven single-purpose layers (the bookstore,
the chime tower, the EV charger, four dining locations, the visitor desk,
admissions, the windmill) that are one concept split across seven layer ids.

## The published dataset

`map.json` and `map.geojson` hold **128 places, 92 with a footprint** — against
Carleton's 124 and 96, which is a fair indication the two datasets are of
comparable use.

| | Places |
| --- | ---: |
| Parking (lots, accessible stalls) | 72 |
| Buildings | 38 |
| Points of interest | 10 |
| Athletic fields | 8 |
| With a prose description | 87 |
| With departments linked | 17 |
| With floor plans | 13 |

The 36 places without a footprint are points: the accessible-parking stalls and
the points of interest. Fourteen more source features — unnamed parking polygons
and blank rows in the athletic-fields layer — are **not** places, because an
unnamed polygon cannot be labelled, searched for or linked to. Their geometry is
still published in `data/`.

### The schema is Carleton's

`carls-app/map-data` publishes `map.json` and `map.geojson`; ccc-server serves
the GeoJSON at `carleton.api.frogpond.tech/v1/map/geojson`; AAO reads it.
Matching that shape means a `stolaf.api.frogpond.tech/v1/map/geojson` is a
configuration change rather than a second code path. Every key Carleton emits is
emitted here, including the ones St. Olaf has nothing to put in, so a consumer
never has to test for a missing key.

Three properties are **added**: `abbreviation`, `type` and `links`. Extra keys
are additive and safe, and dropping St. Olaf's building abbreviations — `RNS`,
`BMC`, `TOH`, the identifiers people on campus actually use — to preserve an
exact field list would be throwing away good data for a bad reason.

### Watch the coordinate order

Carleton's two files disagree with each other. This reproduces that faithfully
rather than fixing it, because consumers are written against it:

| Where | Order |
| --- | --- |
| `map.json` — `center`, `outline` | **latitude first** |
| `map.geojson` — everything | longitude first (GeoJSON, RFC 7946) |
| `overrides.yaml` — `centerpoint` | longitude first |

Swap either and every place lands in the Indian Ocean with no error anywhere, so
`scripts/verify.py` bounds-checks both files in their respective orders. That
check is tested by breaking it.

### Label anchors are computed

Carleton's places carry a **hand-placed** label anchor; that is what its
`overrides.yaml` exists to maintain. St. Olaf's layers have no such point, so one
is derived per place — and the obvious derivation is wrong.

An area-weighted centroid falls *outside* the shape for anything L-shaped or
horseshoe-shaped, which on this campus means Old Main, the Ade Christenson
Complex and most of the lots that wrap a building. A label anchored outside its
own building points at the wrong thing. So `scripts/geometry.py` uses the
centroid when it lands inside the polygon and computes a guaranteed-interior
point otherwise. All 92 polygonal places currently anchor inside their own
footprint, and `verify.py` fails the build if that ever stops being true.

A technically-interior anchor can still read badly. Set a `centerpoint` in
`overrides.yaml` for any that does.

## What St. Olaf does not publish

Carleton's records carry fields St. Olaf has no source for. They are emitted as
`null` or `[]` rather than filled in with guesses.

**Addresses.** Not scraped, though they look scrapeable. Every page on
`wp.stolaf.edu` shows `1520 St. Olaf Avenue, Northfield, MN 55057` — in the
sidebar as an office's mailing address, and again in the site footer as the
college's. It is not the building's address, and no St. Olaf building has one.
Taking it would have stamped the same wrong address onto all twelve residence
halls with nothing in the data to reveal the error.

**Floor plans** *are* scraped, and are the one gap the college's web actually
fills. Each residence hall has a page under `wp.stolaf.edu/residencelife/<slug>/`
carrying labelled floor-plan PDFs, and each building's ArcGIS prose links to its
own page — so the pages to fetch are discovered from the scraped data rather
than hardcoded. Twelve pages, 13 buildings with floors.

**Departments** need no scraping at all: a building's `Information` HTML already
links to the departments it houses, with the department name as the link text.
Tomson Hall lists 21, Regents Hall of Natural Sciences 10. St. Olaf does not
separate academic departments from administrative offices the way Carleton does,
so they all land in `departments` and `offices` stays empty rather than being
filled by guesswork.

**Photos and accessibility** have no source. `photo` is null and `accessibility`
is `"unknown"` throughout.

## `overrides.yaml`

The scrape must stay a faithful copy of what the college publishes — that is
what makes its diff a usable change log. Anything we know better goes in
`overrides.yaml`, where it is reviewable and survives the next scrape. Same job
as Carleton's file of the same name.

```yaml
ids:        # source name -> id, for names that slugify badly
removals:   # ids to drop
changes:    # fields merged onto a place, keyed by id (incl. centerpoint)
additions:  # whole records, in map.json's shape
```

It currently carries three id shortenings and three spelling corrections. The
ArcGIS layer spells two residence halls differently from the college's own
Residence Life pages and from the building signage — `Hillboe` for Hilleboe,
`Kittlesby` for Kittelsby — and calls the Carlson Tennis Courts a "Course", in a
record whose own description then refers to "the courts".

Ids default to the name with its spaces and punctuation removed, in Carleton's
style: `oldmain`, `randhall`, `regentshallofnaturalsciences`. Places from
non-building layers are prefixed (`lot-`, `field-`) because the parking lot
beside Rand Hall is also called Rand. Where several places genuinely share a
name — three separate polygons are all called "Porter" — the ids are numbered
from 1, so no arbitrary member of the group gets to be the unsuffixed one.
`verify.py` lists them, since a numbered id usually means the source wants an
override.

## Determinism is the whole design

Committing scraped data is only worth doing if a diff means something. Three
things protect that, and `verify.py` enforces the third:

- **Volatile fields are dropped.** `OBJECTID`, `FID` and `GlobalID` are database
  identity, not campus data, and renumber wholesale when a layer is republished.
  `Shape__Area` and friends are recomputed server-side.
- **Feature order is imposed** — by name, then by a hash of the geometry — since
  ArcGIS returns features in whatever order it likes.
- **Nothing carries a timestamp.** A "scraped at" field would make every
  scheduled run a commit, which is exactly the noise this avoids. When the data
  last *changed* is the commit date; when it was last *checked* is the workflow
  run history.

Coordinates are rounded to 7 decimals (~1 cm), which is far finer than the
source data's real accuracy and coarse enough that float formatting cannot
produce a spurious diff.

## Running it

Needs [`mise`](https://mise.jdx.dev), which installs Python, uv and actionlint at
exactly the versions CI uses.

```console
$ curl https://mise.run | sh
$ mise install
$ uv sync --locked
$ uv run scripts/scrape.py     # ArcGIS  -> data/*.geojson
$ uv run scripts/enrich.py     # WordPress -> data/residence-life.json
$ uv run scripts/build.py      # + overrides.yaml -> map.json, map.geojson
$ uv run scripts/verify.py
```

`build.py` and `verify.py` need no network. There is one dependency, PyYAML, for
`overrides.yaml`; the ArcGIS client, the HTML extraction and the geometry are all
stdlib, which keeps a job that runs unattended for months off a supply chain it
does not need.

### What `verify.py` checks

Every failure listed here is one this pipeline can actually produce and that
nothing else would catch — the scheduled run has nobody watching it, and the
files it commits are read by an app, not by someone who would notice a building
had moved to Nebraska.

- Both files bounds-checked, each in its own coordinate order.
- Every label anchor inside its own footprint.
- `map.json` and `map.geojson` holding the same places in the same order.
- Every key Carleton emits, present on every record.
- Ids unique; no volatile fields leaked into `data/`; no layer empty.
- `overrides.yaml` entries still matching something.
- **The build is reproducible** — it re-runs `build.py` and fails if the output
  moves.

## Scheduled scraping

`.github/workflows/scrape.yml` runs daily at 06:00 UTC, and on any push that
changes how the data is derived (`scripts/`, `overrides.yaml`, the lockfiles).
It scrapes, builds, verifies, and commits to `main` **only if something
changed** — a no-op commit on every run would destroy the history's value. Every
PR gets a dry run: same pipeline, nothing committed.

Two deliberate details:

- The push trigger excludes `data/`, `map.json` and `map.geojson`, which are
  what the workflow commits. Triggering on them would make it re-trigger itself
  forever.
- The `wp.stolaf.edu` step is `continue-on-error`. The college's WordPress being
  down is not a reason to throw away a good ArcGIS scrape: `build.py` falls back
  to the committed `data/residence-life.json`, floor plans stay at their last
  known good values, everything else still updates, and the run summary says so.

## Not in scope: tiles

[`carls-app/map-tiles`][map-tiles] builds the vector basemap AAO renders, and it
**already covers St. Olaf** — its `CAMPUS_BBOX` spans both campuses and downtown
Northfield, at full z14–z15 detail. What it does not have is a St. Olaf campus
layer; its `campus_buildings` and `campus_building_labels` are Carleton's, built
from Carleton's live endpoint.

So giving AAO a St. Olaf overlay is a change to that repo — two more tippecanoe
inputs reading this repo's `map.geojson` — not a second tileset here. Its
`scripts/campus-layers.py` already unwraps exactly the `GeometryCollection`
shape `map.geojson` emits, because that is Carleton's shape too.

[map-tiles]: https://github.com/carls-app/map-tiles
