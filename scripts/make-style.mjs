// Generate the MapLibre styles this site publishes.
//
//   style.json          plain {z}/{x}/{y}.pbf source — works everywhere
//   style-pmtiles.json  pmtiles:// source — only if the binary was built with
//                       MLN_WITH_PMTILES (see README)
//   style-dark.json     style.json in the dark theme, for a device in dark mode
//
// All three draw the same layers over the same tiles; the first two differ only
// in source, and the dark style only in colour.

import { writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { layers, namedFlavor } from "@protomaps/basemaps";

// Read the version off the package that is actually installed rather than
// restating it in build-tiles.sh. The style and the tile schema have to move
// together, so the number recorded in the style should be the one that
// generated it, not a second copy that can drift.
const basemapsVersion = createRequire(import.meta.url)("@protomaps/basemaps/package.json").version;

const out = process.argv[2];
if (!out) throw new Error("usage: make-style.mjs <outdir>");

const env = (k) => {
  const v = process.env[k];
  if (v === undefined || v === "") throw new Error(`missing env ${k}`);
  return v;
};

const SITE_URL = env("SITE_URL").replace(/\/$/, "");
// The extent of the merged tileset — the widest extraction tier, not the campus
// bbox. MapLibre culls tiles outside a source's `bounds`, so narrowing this to
// campus would suppress every low-zoom context tile.
const [west, south, east, north] = env("DATA_BOUNDS").split(",").map(Number);
const MINZOOM = Number(env("MINZOOM"));
const MAXZOOM = Number(env("MAXZOOM"));
const CENTER = [Number(env("CENTER_LON")), Number(env("CENTER_LAT"))];
const CENTER_ZOOM = Number(env("CENTER_ZOOM"));

// ---------------------------------------------------------------------------
// The St. Olaf theme
// ---------------------------------------------------------------------------
//
// St. Olaf's colours are black and old gold, and the temptation with a brand
// palette is to paint the map with it. That makes a bad basemap: a wayfinding
// map's ground has to recede so the foreground — the app's markers, the user's
// location, a search result — can be read against it. Gold over the whole frame
// leaves nothing for the foreground to be brighter than.
//
// So the brand is spent in exactly one place: **the campus buildings**, which
// are the subject of this map and the one thing here that is St. Olaf's own
// data rather than OpenStreetMap's. Everything else is a warm neutral ground
// tuned to sit underneath it.
//
// Departures from the stock Protomaps "light" flavor:
//
//   earth      Warmed from #e2dfda toward paper. The campus reads as limestone
//              and this keeps the gold from looking sour against a cool grey.
//   water      Stock is a vivid cyan (#80deea). The Cannon River should not be
//              the brightest thing on a screen whose foreground is app-drawn
//              markers. Muted to a calm blue-grey.
//   parks      Stock's saturated park green covers most of the frame — St.
//              Olaf's Natural Lands are 350 acres of the campus. Desaturated so
//              they read as ground rather than as highlighted areas.
//   paths      Stock draws footways at #ebebeb, which is designed to read as a
//              pale line on stock's cooler #e2dfda earth. Against the warmer,
//              lighter earth here it disappears completely — the campus looked
//              like it had no sidewalks at all. Darkened so a path reads as a
//              path on light ground, the way a trail map draws one.
//   buildings  Warmed into the same family as the campus gold, and pulled down
//              in chroma. This one is not a taste call — see "Why the OSM
//              buildings are warm" below.

const light = {
  flavor: {
    ...namedFlavor("light"),

    earth: "#efece4",
    background: "#efece4",

    // Footways and unclassified ways. See "paths" above — this is the single
    // most consequential value in the file for a map people walk around with.
    other: "#e2ddd0",
    bridges_other: "#e2ddd0",

    buildings: "#ded2b4",

    water: "#c2d3dd",
    zoo: "#d6dedd",

    park_a: "#dfe4d9",
    park_b: "#ccd8c6",
    wood_a: "#dce1d5",
    wood_b: "#ccd6c3",
    scrub_a: "#dfe3d8",
    scrub_b: "#ced8c7",

    // Institutional land — the campus is tagged this way. Barely above the
    // earth tone: enough that the campus edge is legible, not enough to compete
    // with the buildings drawn on top of it.
    school: "#eeeade",
    hospital: "#eee7e2",
    industrial: "#e7e8e5",
  },

  campus: {
    // The fill for St. Olaf's own building footprints. Gold pulled well down in
    // chroma: at full brand saturation a 38-polygon layer at z17 is a wall of
    // yellow, and the app still has to draw a selection highlight on top of it
    // that must read as brighter than the resting state.
    buildingFill: "#e6d3a3",
    // A deeper gold edge. The outline is what makes a building read as a
    // discrete object rather than a blob, and it is doing the work the fill
    // deliberately is not.
    buildingLine: "#b3903c",

    // The athletic fields are grass and should read as part of the green.
    athleticsFill: "#d9e2cf",
    athleticsLine: "#b6c7a8",
    // Parking is a neutral hard surface, deliberately close to the earth and
    // *cooler* than it. A lot is a big shape — Buntrock's fills a quarter of
    // the frame at z17 — so it has to be quiet, and it is the outline, not the
    // fill, that makes it read as a lot. The first version of this was a warm
    // grey two values off the OSM building fill, which made every lot look like
    // a building.
    parkingFill: "#e9e8e2",
    parkingLine: "#cdcbc2",

    trail: "#c8cdb6",
    walkway: "#d6cfbd",

    // Warm halo matching the earth, so labels sit on the ground rather than on
    // a grey card.
    labelHalo: "#f4f1e9",
    buildingLabel: "#2f2a24",
    placeLabel: "#4a443b",
    parkingLabel: "#6b645a",
  },

  sprite: "sprite",
};

// The dark theme
//
// The same argument as the light one, run in the other direction: the ground
// recedes and the gold is spent only on the campus buildings.
//
// Departures from the stock Protomaps "dark" flavor:
//
//   earth      Stock is a neutral #1f1f1f with a cooler #34373d background.
//              Warmed to a charcoal in the same family as the light theme's
//              paper, and the background matched to it, so the gold does not
//              sit on a blue-grey.
//   water      A muted blue-grey, but lifted clear of the earth: at a step
//              above it the Cannon River vanished below z13, where it is thin.
//   parks      Dark desaturated greens, for the same reason as the light
//              theme — the Natural Lands are most of the frame.
//   paths      The light theme's paths problem inverted. Stock footways are
//              #333333, which vanishes against a warmer charcoal; lifted to a
//              warm tone that reads as a path on dark ground.
//   buildings  Lifted *above* the earth, where stock sinks them below it, and
//              warmed toward the gold for the same reason as the light theme's.
//   roads      Warmed, and brightened well past the paths. Stock roads sit a
//              few values from its footways, and the campus walkways are about
//              as bright again, so Saint Olaf Drive read as another sidewalk.
//              The light theme gets this for free from stock's white roads.
//   the rest   Labels, boundaries, low-zoom landcover and the remaining greys
//              warmed into the same charcoal family, so nothing on the map is
//              the cool grey the gold was moved off. POI colours stay stock:
//              the sprite icons are pre-coloured per flavor, and a retinted
//              label would no longer match its icon.
//
// The campus buildings flip the light theme's relationship between fill and
// edge. A pale gold fill on dark ground glows, and would leave the app's
// selection highlight nothing to be brighter than; so the fill is a dim,
// low-chroma gold and the outline carries the brand.
const dark = {
  flavor: {
    ...namedFlavor("dark"),

    earth: "#1e1c19",
    background: "#1e1c19",

    other: "#3b372f",
    bridges_other: "#3b372f",

    buildings: "#4a4234",

    water: "#2c3a46",
    zoo: "#222622",

    park_a: "#212620",
    park_b: "#222a21",
    wood_a: "#20251f",
    wood_b: "#212920",
    scrub_a: "#22251f",
    scrub_b: "#232921",

    school: "#23201c",
    hospital: "#241f1e",
    industrial: "#222120",
    pedestrian: "#24211d",
    aerodrome: "#211f1c",
    military: "#24211e",
    sand: "#25221d",
    beach: "#2a2620",
    runway: "#36322c",
    pier: "#36322c",

    // Roads, stepped up from footways to highways. See "roads" above.
    minor_service: "#48433b",
    minor_a: "#5b554c",
    minor_b: "#524c44",
    link: "#5b554c",
    major: "#686156",
    highway: "#756d61",
    bridges_minor: "#524c44",
    bridges_link: "#5b554c",
    bridges_major: "#686156",
    bridges_highway: "#756d61",
    // Casings a step *below* the earth, so a road is edged by shadow rather
    // than by a halo.
    minor_service_casing: "#171512",
    minor_casing: "#171512",
    link_casing: "#171512",
    major_casing_early: "#171512",
    major_casing_late: "#171512",
    highway_casing_early: "#171512",
    highway_casing_late: "#171512",
    bridges_other_casing: "#171512",
    bridges_minor_casing: "#171512",
    bridges_link_casing: "#171512",
    bridges_major_casing: "#171512",
    bridges_highway_casing: "#171512",
    tunnel_other: "#2a2622",
    tunnel_minor: "#2a2622",
    tunnel_link: "#2a2622",
    tunnel_major: "#2a2622",
    tunnel_highway: "#2a2622",

    boundaries: "#5a5248",

    // Labels in the same warm greys, haloed in the earth tone.
    roads_label_minor: "#7a7266",
    roads_label_minor_halo: "#1e1c19",
    roads_label_major: "#8c8376",
    roads_label_major_halo: "#1e1c19",
    subplace_label: "#6f685d",
    subplace_label_halo: "#1e1c19",
    city_label: "#a79e90",
    city_label_halo: "#1e1c19",
    state_label: "#4a453e",
    state_label_halo: "#1e1c19",
    country_label: "#6f685d",
    address_label: "#6f685d",
    address_label_halo: "#1e1c19",
    ocean_label: "#6d7a86",

    // What the map shows below z13, before landuse takes over.
    landcover: {
      ...namedFlavor("dark").landcover,
      grassland: "rgba(33, 38, 30, 1)",
      barren: "rgba(38, 36, 32, 1)",
      urban_area: "rgba(34, 32, 28, 1)",
      farmland: "rgba(32, 34, 28, 1)",
      scrub: "rgba(34, 36, 29, 1)",
      forest: "rgba(29, 37, 30, 1)",
    },
  },

  campus: {
    buildingFill: "#524428",
    buildingLine: "#b8943f",

    athleticsFill: "#253021",
    athleticsLine: "#3e4d35",
    // Cooler than the earth, as in the light theme, so a lot does not read as
    // a building.
    parkingFill: "#27272a",
    parkingLine: "#3c3b38",

    trail: "#454a38",
    walkway: "#4c4638",

    labelHalo: "#1e1c19",
    buildingLabel: "#ece5d8",
    placeLabel: "#c9c1b3",
    parkingLabel: "#9a9285",
  },

  sprite: "sprite-dark",
};

// ---------------------------------------------------------------------------
// St. Olaf's campus layers
// ---------------------------------------------------------------------------
//
// These ride in the same source as the basemap — tile-join merged them into one
// tileset — so they are separate `source-layer`s, not a separate source.
//
// Three layers, not map-tiles' two, because more than half of St. Olaf's places
// are parking. See scripts/campus-layers.py.

const CAMPUS_BUILDINGS_MINZOOM = Number(env("CAMPUS_BUILDINGS_MINZOOM"));
const CAMPUS_GROUNDS_MINZOOM = Number(env("CAMPUS_GROUNDS_MINZOOM"));
const CAMPUS_PATHS_MINZOOM = Number(env("CAMPUS_PATHS_MINZOOM"));
const CAMPUS_LABELS_MINZOOM = Number(env("CAMPUS_LABELS_MINZOOM"));
const OSM_BUILDINGS = env("OSM_BUILDINGS");
if (!["full", "ghost", "off"].includes(OSM_BUILDINGS)) {
  throw new Error(`OSM_BUILDINGS must be one of full, ghost, off — got "${OSM_BUILDINGS}"`);
}

const parseHex = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

const flatten = (fg, bg, alpha) => {
  if (typeof alpha !== "number") return fg; // an expression: leave it be
  const [fr, fg_, fb] = parseHex(fg);
  const [br, bg_, bb] = parseHex(bg);
  const mix = (f, b) => Math.round(b * (1 - alpha) + f * alpha);
  return (
    "#" +
    [mix(fr, br), mix(fg_, bg_), mix(fb, bb)].map((v) => v.toString(16).padStart(2, "0")).join("")
  );
};

// Why the OSM buildings are warm
//
// Two building layers overlap on campus, and where two datasets disagree about
// an outline the difference has to read as one building rather than as a
// registration error. carls-app/map-tiles solves that for Carleton by painting
// both layers a single colour, because Carleton's hand-drawn footprints and
// OSM's disagree substantially.
//
// St. Olaf's disagree very little. Rendering the tileset with this layer forced
// to pure red shows the overhang directly: across the campus core it is a
// handful of narrow strips, the widest being two slivers along the west edge of
// Rolvaag Memorial Library. Almost every OSM footprint on campus sits entirely
// under a college polygon, which is what lets the campus layer take a colour of
// its own.
//
// This layer still shares that colour's hue family rather than taking a neutral
// grey, so a visible sliver reads as part of the same building, and so downtown
// Northfield — which has no campus data at all — sits in the same palette.
//
// Worth knowing when reading the map: the pale shapes beside New Hall and
// Rolvaag that look like duplicate buildings are OSM's `school` landuse, which
// blankets the campus, plus this repo's own `campus_grounds`. Neither is a
// building layer.
const campusLayers = (campus) => [
  {
    id: "campus_grounds",
    type: "fill",
    source: "basemap",
    "source-layer": "campus_grounds",
    minzoom: CAMPUS_GROUNDS_MINZOOM,
    paint: {
      // One layer, two materials: athletic fields, and parking.
      "fill-color": [
        "match",
        ["get", "kind"],
        "athletics",
        campus.athleticsFill,
        // parking, and anything new that lands in this layer
        campus.parkingFill,
      ],
      "fill-opacity": 1,
    },
  },
  {
    id: "campus_grounds_outline",
    type: "line",
    source: "basemap",
    "source-layer": "campus_grounds",
    minzoom: CAMPUS_GROUNDS_MINZOOM + 1,
    paint: {
      "line-color": [
        "match",
        ["get", "kind"],
        "athletics",
        campus.athleticsLine,
        campus.parkingLine,
      ],
      "line-width": ["interpolate", ["linear"], ["zoom"], 15, 0.4, 18, 1],
      "line-opacity": 0.9,
    },
  },
  // The college's own walkways and Natural Lands trails.
  //
  // OSM has good footway coverage over the campus core, but the college's
  // walkway layer is 138 lines and 10.8 km — comparable to the pedestrian graph
  // in StoDevX/ole-compass — and its Natural Lands trails add another 12 km
  // that OSM largely does not have. On a map people walk a campus with, that is
  // worth drawing.
  //
  // Drawn over `campus_grounds` so a walk through a parking lot still reads,
  // and under `campus_buildings` so nothing crosses a building.
  {
    id: "campus_paths",
    type: "line",
    source: "basemap",
    "source-layer": "campus_paths",
    minzoom: CAMPUS_PATHS_MINZOOM,
    paint: {
      "line-color": ["match", ["get", "kind"], "trail", campus.trail, campus.walkway],
      "line-width": ["interpolate", ["exponential", 1.6], ["zoom"], 15, 0.5, 20, 6],
      "line-opacity": 0.95,
    },
  },
  {
    id: "campus_buildings",
    type: "fill",
    source: "basemap",
    "source-layer": "campus_buildings",
    minzoom: CAMPUS_BUILDINGS_MINZOOM,
    paint: { "fill-color": campus.buildingFill, "fill-opacity": 1 },
  },
  {
    id: "campus_buildings_outline",
    type: "line",
    source: "basemap",
    "source-layer": "campus_buildings",
    // A zoom later than the fill: at z14 a building is a few pixels across and
    // an outline on it is just noise that darkens the campus.
    minzoom: CAMPUS_BUILDINGS_MINZOOM + 1,
    paint: {
      "line-color": campus.buildingLine,
      "line-width": ["interpolate", ["linear"], ["zoom"], 15, 0.4, 18, 1.2],
      "line-opacity": 0.9,
    },
  },
];

// Labels, split by what the place is so each kind can appear at the zoom where
// it stops being clutter. This is the whole reason `kind` is tiled onto the
// points: 72 of the 128 places are parking, and showing them at the zoom where
// you want building names buries the campus in lot names.
const labelLayer = (id, kinds, minzoom, { size, color, halo, weight }) => ({
  id,
  type: "symbol",
  source: "basemap",
  "source-layer": "campus_labels",
  minzoom,
  filter: ["match", ["get", "kind"], kinds, true, false],
  layout: {
    "text-field": ["get", "name"],
    // Must name a stack the site actually hosts under `glyphs`. A stack that is
    // not there renders no labels and reports nothing; verify-tiles.py checks.
    "text-font": [weight],
    "text-size": size,
    "text-anchor": "center",
    "text-max-width": 8,
    "text-padding": 2,
  },
  paint: {
    "text-color": color,
    "text-halo-color": halo,
    "text-halo-width": 1.2,
  },
});

const campusLabelLayers = (campus) => [
  // Parking last in the array so it ends up lowest in the label stack: when
  // MapLibre has to drop a colliding label, the lot name goes before the
  // building name.
  labelLayer("campus_labels_buildings", ["building"], CAMPUS_LABELS_MINZOOM, {
    size: ["interpolate", ["linear"], ["zoom"], 15, 10.5, 18, 13.5],
    color: campus.buildingLabel,
    halo: campus.labelHalo,
    weight: "Noto Sans Medium",
  }),
  labelLayer(
    "campus_labels_places",
    ["point-of-interest", "athletics"],
    CAMPUS_LABELS_MINZOOM + 1,
    {
      size: ["interpolate", ["linear"], ["zoom"], 16, 10, 18, 12],
      color: campus.placeLabel,
      halo: campus.labelHalo,
      weight: "Noto Sans Regular",
    },
  ),
  labelLayer("campus_labels_parking", ["parking"], CAMPUS_LABELS_MINZOOM + 2, {
    size: ["interpolate", ["linear"], ["zoom"], 17, 9.5, 19, 11],
    color: campus.parkingLabel,
    halo: campus.labelHalo,
    weight: "Noto Sans Italic",
  }),
];

// The whole layer list for one theme: the Protomaps basemap in the theme's
// flavor, with St. Olaf's campus layers spliced into it.
const styleLayersFor = ({ flavor, campus }) => {
  const styleLayers = layers("basemap", flavor, { lang: "en" });

  // Where they go: fills above the basemap's own buildings, labels below its
  // address labels. Found by layer id rather than index, so an upstream
  // reshuffle of the Protomaps layer list cannot silently put them somewhere
  // else.
  const indexOf = (id) => {
    const i = styleLayers.findIndex((l) => l.id === id);
    if (i < 0) throw new Error(`expected a basemap layer called ${id}`);
    return i;
  };

  const stockBuildings = styleLayers[indexOf("buildings")];

  // The stock buildings layer is half-transparent. Flattened against the earth
  // beneath it and painted opaque, so that the campus layer drawn on top
  // composes against a known colour rather than against whatever happens to be
  // underneath.
  const osmBuildingFill = flatten(
    stockBuildings.paint["fill-color"],
    flavor.earth,
    stockBuildings.paint["fill-opacity"],
  );

  // Labels go in first, at the higher index, so inserting the fills below them
  // does not shift the position that was just computed.
  styleLayers.splice(indexOf("address_label"), 0, ...campusLabelLayers(campus));
  styleLayers.splice(indexOf("buildings") + 1, 0, ...campusLayers(campus));

  // How much of the basemap's own OSM building layer to draw.
  //
  //   full   draw it normally — the default
  //   ghost  fade it from CAMPUS_BUILDINGS_MINZOOM so only St. Olaf's read
  //   off    omit it entirely
  //
  // `full` is the default because downtown Northfield has no campus data at all
  // and would otherwise lose its buildings. The other two exist because these
  // are the densest polygons on the map: at z17 over campus the renderer draws
  // every OSM footprint plus every campus one, and dropping the OSM layer
  // roughly halves that without touching `campus_buildings`, which the app
  // hit-tests taps against and therefore cannot hide.
  stockBuildings.paint = { "fill-color": osmBuildingFill, "fill-opacity": 1 };
  if (OSM_BUILDINGS === "off") {
    styleLayers.splice(indexOf("buildings"), 1);
  } else if (OSM_BUILDINGS === "ghost") {
    stockBuildings.paint = {
      ...stockBuildings.paint,
      "fill-opacity": [
        "interpolate",
        ["linear"],
        ["zoom"],
        CAMPUS_BUILDINGS_MINZOOM - 1,
        1,
        CAMPUS_BUILDINGS_MINZOOM + 1,
        0.35,
      ],
    };
  }

  return styleLayers;
};

// Defined once in build-tiles.sh, so the styles and the archive's own metadata
// cannot drift apart. St. Olaf's building data is the college's, not
// OpenStreetMap's, and is credited separately for that reason.
const ATTRIBUTION = env("ATTRIBUTION");

const styleFor = (theme) => ({
  version: 8,
  name: "St. Olaf Campus Basemap",
  metadata: {
    "stolaf:generated-by": "StoDevX/campus-map-data",
    "stolaf:schema": "protomaps basemap v4",
    "stolaf:style-package": `@protomaps/basemaps@${basemapsVersion}`,
    "stolaf:campus-layers": "campus_buildings, campus_grounds, campus_paths, campus_labels",
    "stolaf:osm-buildings": OSM_BUILDINGS,
    "stolaf:note":
      "OSM basemap plus St. Olaf's own footprints and label anchors, from this " +
      "repo's map.geojson (scraped from the college's ArcGIS services). Every " +
      "campus layer carries buildingId. The app draws its selection highlight on top.",
  },
  center: CENTER,
  zoom: CENTER_ZOOM,
  bearing: 0,
  pitch: 0,
  glyphs: `${SITE_URL}/fonts/{fontstack}/{range}.pbf`,
  // Each Protomaps flavor has its own sprite sheet, drawn for its ground.
  sprite: `${SITE_URL}/sprites/${theme.sprite}`,
  layers: styleLayersFor(theme),
});

// The tileset stops at MAXZOOM; MapLibre scales those tiles beyond it rather
// than requesting tiles that do not exist. Nothing here caps how far the user
// can zoom — the style spec has no property for it, so that is the map view's
// maxZoomLevel, set by the app.
const sourceCommon = {
  type: "vector",
  attribution: ATTRIBUTION,
  bounds: [west, south, east, north],
};

const tileSource = {
  basemap: {
    ...sourceCommon,
    tiles: [`${SITE_URL}/tiles/{z}/{x}/{y}.pbf`],
    minzoom: MINZOOM,
    maxzoom: MAXZOOM,
  },
};

const pmtilesSource = {
  basemap: { ...sourceCommon, url: `pmtiles://${SITE_URL}/campus.pmtiles` },
};

const lightStyle = styleFor(light);
const darkStyle = styleFor(dark);

// The dark style is z/x/y only: it is for the app, and the app fetches z/x/y.
const styles = {
  "style.json": { ...lightStyle, sources: tileSource },
  "style-pmtiles.json": {
    ...lightStyle,
    name: `${lightStyle.name} (PMTiles)`,
    sources: pmtilesSource,
  },
  "style-dark.json": { ...darkStyle, name: `${darkStyle.name} (Dark)`, sources: tileSource },
};

console.log(
  `  campus layers       campus_buildings (z${CAMPUS_BUILDINGS_MINZOOM}+), campus_grounds (z${CAMPUS_GROUNDS_MINZOOM}+), campus_paths (z${CAMPUS_PATHS_MINZOOM}+), campus_labels (z${CAMPUS_LABELS_MINZOOM}+), OSM buildings: ${OSM_BUILDINGS}`,
);
for (const [file, style] of Object.entries(styles)) {
  writeFileSync(join(out, file), JSON.stringify(style, null, 2) + "\n");
  const source = style.sources.basemap;
  const from = source.url ?? `tiles/{z}/{x}/{y}.pbf, z${MINZOOM}-z${MAXZOOM}`;
  console.log(`  ${file.padEnd(19)} ${style.layers.length} layers, ${from}`);
}

// Guard against the failure the README warns about: a style naming a fontstack
// the site does not host renders with no labels and no error.
const fonts = new Set();
const walk = (v) => {
  if (Array.isArray(v)) v.forEach(walk);
  else if (v && typeof v === "object") Object.values(v).forEach(walk);
  else if (typeof v === "string") fonts.add(v);
};
for (const style of Object.values(styles)) {
  for (const l of style.layers) if (l.layout?.["text-font"]) walk(l.layout["text-font"]);
}
const expected = new Set(["Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic"]);
const named = [...fonts].filter((f) => f.startsWith("Noto Sans"));
const missing = named.filter((f) => !expected.has(f));
if (missing.length) {
  throw new Error(`style names fontstacks the build does not vendor: ${missing.join(", ")}`);
}
console.log(`  fontstacks verified: ${named.toSorted().join(", ")}`);
