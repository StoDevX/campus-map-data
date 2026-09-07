#!/usr/bin/env bash
#
# Build the St. Olaf campus basemap.
#
# Pulls tiles out of the Protomaps daily planet build over HTTP range requests
# (no planet download, no OSM processing), joins this repo's own campus layers
# into them, and lays out a GitHub Pages site containing the result twice — as a
# single PMTiles archive and as an exploded {z}/{x}/{y}.pbf directory — plus the
# glyphs, sprites and styles the map needs.
#
# The site also carries the campus *data* — map.json, map.geojson, data/ — so
# ccc-server has a URL to fetch, the same way carls-app/map-data serves
# Carleton's. That is why this publishes the data files it did not build.
#
# Everything lands in ./dist, which is what gets uploaded and deployed to Pages.
#
# Adapted from carls-app/map-tiles, which does the same job for Carleton.
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration — change these and nothing else.
# ---------------------------------------------------------------------------

# Two areas, in west,south,east,north order.
#
# These match carls-app/map-tiles deliberately. AAO is one app over two
# colleges, students move between the campuses and through the town, and
# matching bboxes means this tileset and that one are interchangeable rather
# than each being wrong outside its own campus.
REGION_BBOX="-93.50,44.28,-92.84,44.75"
CAMPUS_BBOX="-93.28,44.38,-93.05,44.55"

# Zoom range. The Protomaps planet build tops out at z15, so MAXZOOM cannot
# usefully exceed that — MapLibre overzooms z15 tiles beyond it. How far the
# user may pinch in is not set here and cannot be: the style spec has no such
# property. It is the map view's own maxZoomLevel, in the app.
MINZOOM=0
MAXZOOM=15

# St. Olaf's own campus data, joined into the tileset as three extra layers on
# top of the OSM basemap. This is the college's, not OpenStreetMap's, and is
# credited separately in the styles.
#
# A local file, not a URL. map-tiles fetches Carleton's from a live endpoint
# because its data lives in another repo; ours is right here, produced by the
# same CI run. That removes a network dependency and makes it impossible for the
# tiles to be built from a different revision of the data than the one published
# beside them.
CAMPUS_GEOJSON="map.geojson"

# Footprints only need to exist where they are legible, and the labels later so
# they do not pile up. Grounds come in with the buildings: a parking lot with no
# fill at the zoom its neighbours have one looks like missing data.
CAMPUS_BUILDINGS_MINZOOM=14
CAMPUS_GROUNDS_MINZOOM=14
# Paths a zoom later than the fills: at z14 the campus is ~120 px across and the
# walkway network is an unreadable smudge at any line width.
CAMPUS_PATHS_MINZOOM=15
CAMPUS_LABELS_MINZOOM=15

# How finely campus geometry is stored, as a power of two: 14 means 16384 units
# per tile instead of tippecanoe's default 4096.
#
# Quantisation is tile size over extent, so precision can be bought either by
# adding zoom levels (shrinking the numerator) or by raising the extent. At z15
# the default 4096 quantises to ~21 cm — one pixel at z18, four at z20. Extent
# 16384 gets that to ~5.3 cm, sharp past z21, for about a kilobyte.
CAMPUS_DETAIL=14

# What to do with the basemap's own OSM building footprints, which overlap the
# campus ones. See scripts/make-style.mjs for why a distinct campus colour is
# safe here where map-tiles had to match: full, ghost, off.
OSM_BUILDINGS="full"

# The extract is graduated: the whole region down to street level, then only the
# campus area for the zooms where tiles get expensive. Going one zoom deeper
# across the whole region costs more than the entire rest of the pyramid, and
# nothing in a campus wayfinding app needs building footprints in Faribault.
#
# Each tier is "minzoom:maxzoom:bbox". They must be contiguous and cover
# MINZOOM..MAXZOOM; the build checks that and refuses to start otherwise.
TIERS=(
  "0:13:$REGION_BBOX"   # whole region, down to street level
  "14:15:$CAMPUS_BBOX"  # campuses, Northfield and Dundas, in full detail
)

# Where the built site is served from. Written into the styles as absolute URLs,
# since MapLibre Native resolves style-relative URLs inconsistently.
#
# stolaf.dev, not stodevx.github.io. The StoDevX Pages site has a custom domain,
# so GitHub 301s every project page to `stolaf.dev/<repo>/` — and these URLs are
# baked into style.json for the glyphs, the sprites and every tile. Pointing them
# at the redirecting host would put an extra round trip in front of each of the
# ~985 tile requests, on a client (MapLibre Native) whose redirect handling is
# not something this repo can test. The canonical host serves the same files
# with no hop.
SITE_URL="${SITE_URL:-https://stolaf.dev/campus-map-data}"

# Credited on every published form: the styles' source `attribution`, and the
# archive's own metadata. St. Olaf's campus data is the college's, not
# OpenStreetMap's, so it is named separately. ODbL requires the OSM half.
ATTRIBUTION='<a href="https://www.openstreetmap.org/copyright" target="_blank">&copy; OpenStreetMap contributors</a> | <a href="https://wp.stolaf.edu/" target="_blank">St. Olaf College</a>'

# Map opens on Manitou Heights — the campus boundary's own interior point.
CENTER_LON=-93.1847
CENTER_LAT=44.4613
CENTER_ZOOM=15

# The one version pin left here: the font and sprite assets are a repo without
# releases, so it tracks the tip of its default branch by digest. Renovate
# matches on the comment, so keep the two attached.
# renovate: datasource=git-refs depName=https://github.com/protomaps/basemaps-assets branch=main
ASSETS_COMMIT="028c18f713baecad011301ff7a69acc39bcc2ae7"

# The Protomaps daily builds are retained for about a week and there is no index
# to list them, so probe backwards from today until one answers.
BUILD_LOOKBACK_DAYS=10

# Hard ceiling on a single file served by GitHub Pages. Git LFS is not a way
# around this — Pages serves the LFS pointer file, not the object.
PAGES_FILE_LIMIT=100000000

# Floors, which are the other end of the same question.
#
# Every other check in this build catches a *structural* failure: a layer the
# style names but the tileset lacks, a source feature that reached no layer, a
# gzipped tile. None of them notice a build that is perfectly well-formed and
# simply has far less in it than it should — which is what a degraded upstream
# looks like. A truncated planet build sails through, the workflow deploys the
# result over a good one, and since something now renders, it stays broken until
# someone looks.
#
# These are tripwires, not targets: set below the current numbers so ordinary
# drift never trips them. A bbox or zoom change in TIERS moves the tile count
# and archive size, and is expected to move these with it.
MIN_CAMPUS_BUILDINGS=34   # currently 38
MIN_CAMPUS_GROUNDS=48     # currently 54
MIN_CAMPUS_LABELS=115     # currently 128
MIN_CAMPUS_PATHS=2        # walkways and trails, one merged feature each
MIN_TILES=900             # currently ~985
MIN_ARCHIVE_BYTES=5000000 # currently ~6.4 MB

# ---------------------------------------------------------------------------

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST="$ROOT/dist"
WORK="$ROOT/.work"

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

mkdir -p "$WORK"

# --- 0. check the tier config ----------------------------------------------
#
# TIERS is the one thing here meant to be edited, and the two ways to get it
# wrong are both silent. A gap in the zoom coverage yields a map that goes blank
# at one zoom and comes back at the next; a `bounds` narrower than the data
# culls tiles that were paid for. So validate the tiers, and derive the data
# extent from them rather than restating it by hand.
DATA_BOUNDS="$(
  MINZOOM="$MINZOOM" MAXZOOM="$MAXZOOM" python3 - "${TIERS[@]}" <<'PY'
import os, sys

tiers = []
for spec in sys.argv[1:]:
    lo, hi, bbox = spec.split(":", 2)
    w, s, e, n = (float(v) for v in bbox.split(","))
    if not (w < e and s < n):
        sys.exit(f"tier {spec!r}: bbox is not west,south,east,north")
    tiers.append((int(lo), int(hi), (w, s, e, n)))

tiers.sort()
want = int(os.environ["MINZOOM"])
for lo, hi, _ in tiers:
    if lo != want:
        sys.exit(f"tier zoom coverage breaks at z{want}: next tier starts at z{lo}")
    if hi < lo:
        sys.exit(f"tier z{lo}-z{hi} has maxzoom below minzoom")
    want = hi + 1
if want - 1 != int(os.environ["MAXZOOM"]):
    sys.exit(f"tiers cover up to z{want - 1}, but MAXZOOM is z{os.environ['MAXZOOM']}")

# The union of every tier — what the tileset actually spans.
print("%s,%s,%s,%s" % (
    min(t[2][0] for t in tiers), min(t[2][1] for t in tiers),
    max(t[2][2] for t in tiers), max(t[2][3] for t in tiers),
))
PY
)"

# --- 1. tools --------------------------------------------------------------

log "Fetching tools"

command -v mise >/dev/null || {
  echo "ERROR: mise not found. It manages this repo's toolchain (see mise.toml):" >&2
  echo "  curl https://mise.run | sh" >&2
  exit 1
}
mise install
mise exec -- pmtiles version
mise exec -- uv --version

PMTILES="mise exec -- pmtiles"

# Run a project script against the locked Python environment.
py() { mise exec -- uv run --quiet --project "$ROOT" "$@"; }

# tippecanoe tiles the campus layers and tile-join merges them into the basemap.
# Unlike the rest of the toolchain it is not fetched here — it is a C++ build
# with no prebuilt releases, and every platform already packages it.
for tool in tippecanoe tile-join; do
  command -v "$tool" >/dev/null || {
    echo "ERROR: $tool not found. Install tippecanoe:" >&2
    echo "  Debian/Ubuntu: sudo apt-get install -y tippecanoe" >&2
    echo "  macOS:         brew install tippecanoe" >&2
    exit 1
  }
done
tippecanoe --version 2>&1 | head -1

# --- 2. find a planet build ------------------------------------------------

log "Locating a Protomaps daily build"

PLANET_URL=""
for i in $(seq 0 "$BUILD_LOOKBACK_DAYS"); do
  if date -v-1d >/dev/null 2>&1; then
    day="$(date -u -v-"${i}"d +%Y%m%d)"      # BSD date (macOS)
  else
    day="$(date -u -d "-${i} days" +%Y%m%d)" # GNU date
  fi
  url="https://build.protomaps.com/${day}.pmtiles"
  code="$(curl -sS -m 30 -o /dev/null -w '%{http_code}' -r 0-99 "$url" || true)"
  if [ "$code" = "206" ] || [ "$code" = "200" ]; then
    PLANET_URL="$url"
    echo "using $url"
    break
  fi
  echo "  $day -> $code"
done
[ -n "$PLANET_URL" ] || { echo "no Protomaps build found in the last $BUILD_LOOKBACK_DAYS days" >&2; exit 1; }

# --- 3. extract each tier --------------------------------------------------

log "Extracting ${#TIERS[@]} zoom tiers"

rm -rf "$DIST" "$WORK/tiers"
mkdir -p "$DIST" "$WORK/tiers"

TIER_FILES=()
for tier in "${TIERS[@]}"; do
  minz="${tier%%:*}"
  rest="${tier#*:}"
  maxz="${rest%%:*}"
  tbbox="${rest#*:}"
  f="$WORK/tiers/z${minz}-${maxz}.pmtiles"
  echo "  z${minz}-z${maxz}  $tbbox"
  $PMTILES extract "$PLANET_URL" "$f" \
    --bbox="$tbbox" --minzoom="$minz" --maxzoom="$maxz" 2>&1 |
    grep -E 'Extract transferred|Region tiles' | sed 's/^/    /'
  TIER_FILES+=("$f")
done

# --- 4. merge the tiers ----------------------------------------------------

log "Merging the basemap tiers"

# `pmtiles merge` requires its inputs to be disjoint and refuses otherwise,
# which turns "the tiers must not overlap" from an assumption into something the
# build enforces.
rm -f "$WORK/basemap.pmtiles"
$PMTILES merge "${TIER_FILES[@]}" "$WORK/basemap.pmtiles" --quiet
$PMTILES show "$WORK/basemap.pmtiles" | grep -E 'zoom|count' | sed 's/^/  /'

# --- 5. St. Olaf's campus layers -------------------------------------------

log "Building the campus layers"

[ -f "$ROOT/$CAMPUS_GEOJSON" ] || {
  echo "ERROR: $CAMPUS_GEOJSON not found. Run the data pipeline first:" >&2
  echo "  uv run scripts/scrape.py && uv run scripts/build.py" >&2
  exit 1
}

MIN_CAMPUS_BUILDINGS="$MIN_CAMPUS_BUILDINGS" \
MIN_CAMPUS_GROUNDS="$MIN_CAMPUS_GROUNDS" \
MIN_CAMPUS_LABELS="$MIN_CAMPUS_LABELS" \
MIN_CAMPUS_PATHS="$MIN_CAMPUS_PATHS" \
  py "$ROOT/scripts/campus-layers.py" "$ROOT/$CAMPUS_GEOJSON" "$ROOT/data" "$WORK"

# Nothing may be dropped: there are barely a hundred features and every one is a
# place someone might be trying to find. Hence --no-feature-limit and
# --no-tile-size-limit, and emphatically not --drop-densest-as-needed. Points
# are also dropped by default below the base zoom, which --drop-rate=1 disables.
for layer in campus_buildings campus_grounds; do
  tippecanoe -q -f -o "$WORK/$layer.pmtiles" \
    --layer="$layer" \
    --minimum-zoom="$CAMPUS_BUILDINGS_MINZOOM" --maximum-zoom="$MAXZOOM" \
    --full-detail="$CAMPUS_DETAIL" \
    --no-feature-limit --no-tile-size-limit --no-tiny-polygon-reduction \
    "$WORK/$layer.geojson"
done

tippecanoe -q -f -o "$WORK/campus_paths.pmtiles" \
  --layer=campus_paths \
  --minimum-zoom="$CAMPUS_PATHS_MINZOOM" --maximum-zoom="$MAXZOOM" \
  --full-detail="$CAMPUS_DETAIL" \
  --no-feature-limit --no-tile-size-limit --no-line-simplification \
  "$WORK/campus_paths.geojson"

tippecanoe -q -f -o "$WORK/campus_labels.pmtiles" \
  --layer=campus_labels \
  --minimum-zoom="$CAMPUS_LABELS_MINZOOM" --maximum-zoom="$MAXZOOM" \
  --full-detail="$CAMPUS_DETAIL" \
  --no-feature-limit --no-tile-size-limit --drop-rate=1 \
  "$WORK/campus_labels.geojson"

# --- 6. join into both published forms -------------------------------------

log "Joining campus layers into the basemap"

# One archive and one tile tree for the app, rather than several sources to
# juggle. tile-join reads and writes PMTiles as well as MBTiles, so this goes
# straight to the published archive with no staging format in between.
tile-join -f -pk -A "$ATTRIBUTION" -o "$DIST/campus.pmtiles" \
  "$WORK/basemap.pmtiles" \
  "$WORK/campus_buildings.pmtiles" \
  "$WORK/campus_grounds.pmtiles" \
  "$WORK/campus_paths.pmtiles" \
  "$WORK/campus_labels.pmtiles" >/dev/null

$PMTILES verify "$DIST/campus.pmtiles"
$PMTILES show "$DIST/campus.pmtiles" | grep -E 'zoom|bounds|tile type|compression|count' | sed 's/^/  /'

# The exploded tree comes from the joined archive, so the two published forms
# are the same tileset by construction.
log "Exploding to tiles/{z}/{x}/{y}.pbf (uncompressed)"
py "$ROOT/scripts/explode.py" "$DIST/campus.pmtiles" "$DIST/tiles"

# --- 7. glyphs and sprites -------------------------------------------------
#
# A style that names a font or icon it cannot fetch renders without labels
# instead of erroring, so these are self-hosted alongside the tiles.

log "Vendoring glyphs and sprites"

# Fetch just the pinned commit rather than the whole history — the repo is
# mostly font binaries and its history is much larger than its tree.
ASSETS="$WORK/basemaps-assets"
if [ ! -e "$ASSETS/.git" ]; then
  mkdir -p "$ASSETS"
  git -C "$ASSETS" init --quiet
  git -C "$ASSETS" remote add origin https://github.com/protomaps/basemaps-assets.git
fi
if ! git -C "$ASSETS" cat-file -e "${ASSETS_COMMIT}^{commit}" 2>/dev/null; then
  git -C "$ASSETS" fetch --quiet --depth 1 origin "$ASSETS_COMMIT"
fi
git -C "$ASSETS" checkout --quiet --force "$ASSETS_COMMIT"

mkdir -p "$DIST/fonts" "$DIST/sprites"
# The three stacks the style names, and every range of each. Only the Latin
# ranges get requested around Northfield, but the z0-z8 tier carries global city
# and country labels, so any script can show up when the user pinches out.
for stack in "Noto Sans Regular" "Noto Sans Medium" "Noto Sans Italic"; do
  cp -R "$ASSETS/fonts/$stack" "$DIST/fonts/$stack"
done
cp "$ASSETS/fonts/OFL.txt" "$DIST/fonts/OFL.txt"

# The style is derived from the Protomaps "light" flavor, so the light sprite
# sheet is the matching one. Both densities: iOS is a 2x/3x device.
cp "$ASSETS/sprites/v4/light.json"     "$DIST/sprites/sprite.json"
cp "$ASSETS/sprites/v4/light.png"      "$DIST/sprites/sprite.png"
cp "$ASSETS/sprites/v4/light@2x.json"  "$DIST/sprites/sprite@2x.json"
cp "$ASSETS/sprites/v4/light@2x.png"   "$DIST/sprites/sprite@2x.png"

# --- 8. styles -------------------------------------------------------------

log "Generating styles"

( cd "$ROOT" && { npm ci --silent 2>/dev/null || npm install --silent; } )

SITE_URL="$SITE_URL" \
DATA_BOUNDS="$DATA_BOUNDS" \
MINZOOM="$MINZOOM" \
MAXZOOM="$MAXZOOM" \
CENTER_LON="$CENTER_LON" CENTER_LAT="$CENTER_LAT" CENTER_ZOOM="$CENTER_ZOOM" \
ATTRIBUTION="$ATTRIBUTION" \
CAMPUS_BUILDINGS_MINZOOM="$CAMPUS_BUILDINGS_MINZOOM" \
CAMPUS_GROUNDS_MINZOOM="$CAMPUS_GROUNDS_MINZOOM" \
CAMPUS_PATHS_MINZOOM="$CAMPUS_PATHS_MINZOOM" \
CAMPUS_LABELS_MINZOOM="$CAMPUS_LABELS_MINZOOM" \
OSM_BUILDINGS="$OSM_BUILDINGS" \
  node "$ROOT/scripts/make-style.mjs" "$DIST"

cp "$ROOT/site/index.html" "$DIST/index.html"
cp "$ROOT/site/.nojekyll" "$DIST/.nojekyll"

# Every mismatch this catches — style against schema, style against vendored
# assets — renders a blank or label-less map without raising an error, which is
# miserable to debug on device.
py "$ROOT/scripts/verify-tiles.py" "$DIST"

# --- 9. the campus data ----------------------------------------------------
#
# Published alongside the tiles because Pages serves one branch, and ccc-server
# needs a URL for map.geojson. Copied rather than rebuilt: these are the files
# committed to main, and shipping the same bytes is what makes the tiles and the
# data provably the same revision.

log "Publishing the campus data alongside the tiles"

cp "$ROOT/map.json" "$ROOT/map.geojson" "$DIST/"
cp -R "$ROOT/data" "$DIST/data"
published="map.json, map.geojson, data/"
# The routing graph, when one has been built. Optional so a checkout that has
# not run build_routing.py still publishes a valid site.
if [ -f "$ROOT/routing.json" ]; then
  cp "$ROOT/routing.json" "$DIST/"
  published="$published, routing.json"
fi
echo "  $published ($(find "$DIST/data" -type f | wc -l | tr -d ' ') data files)"

# --- 10. report ------------------------------------------------------------

log "Output"

TILE_COUNT="$(find "$DIST/tiles" -name '*.pbf' | wc -l | tr -d ' ')"
FONT_COUNT="$(find "$DIST/fonts" -name '*.pbf' | wc -l | tr -d ' ')"
{
  echo "campus.pmtiles : $(du -h "$DIST/campus.pmtiles" | cut -f1)"
  echo "tiles/         : $(du -sh "$DIST/tiles" | cut -f1) across $TILE_COUNT files"
  echo "fonts/         : $(du -sh "$DIST/fonts" | cut -f1) across $FONT_COUNT ranges"
  echo "sprites/       : $(du -sh "$DIST/sprites" | cut -f1)"
  echo "data/          : $(du -sh "$DIST/data" | cut -f1)"
  echo "site total     : $(du -sh "$DIST" | cut -f1)"
} | sed 's/^/  /'

SIZE_BYTES="$(wc -c < "$DIST/campus.pmtiles" | tr -d ' ')"
if [ "$SIZE_BYTES" -gt "$PAGES_FILE_LIMIT" ]; then
  echo "ERROR: campus.pmtiles is $SIZE_BYTES bytes, over the 100 MB GitHub Pages per-file limit." >&2
  echo "Shrink a tier's bbox or zoom range, or move the archive to a Release asset." >&2
  exit 1
fi

# And the floors. Checked against the finished output rather than each stage, so
# it does not matter which step came up short — a bad extract, a merge that lost
# a tier, an explode that wrote nothing.
SHORT=0
if [ "$TILE_COUNT" -lt "$MIN_TILES" ]; then
  echo "ERROR: $TILE_COUNT tiles in dist/tiles, floor is $MIN_TILES." >&2
  SHORT=1
fi
if [ "$SIZE_BYTES" -lt "$MIN_ARCHIVE_BYTES" ]; then
  echo "ERROR: campus.pmtiles is $SIZE_BYTES bytes, floor is $MIN_ARCHIVE_BYTES." >&2
  SHORT=1
fi
if [ "$SHORT" != 0 ]; then
  echo "Refusing to publish a build this much smaller than expected — see the" >&2
  echo "floors in build-tiles.sh. If you changed a bbox or a zoom in TIERS, that is" >&2
  echo "the cause and MIN_TILES/MIN_ARCHIVE_BYTES want moving to match." >&2
  exit 1
fi

log "Done — $DIST is ready to publish"
