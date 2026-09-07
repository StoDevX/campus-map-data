#!/usr/bin/env python3
"""Georeference ole-compass's pedestrian graph into `routing.json`.

`campus_paths` in the tileset draws the college's walkways, and that is all it
can do: the ArcGIS layers are undirected linework with no connectivity model.
Two lines that cross on screen may or may not join, and nothing says where a
building's door is. You cannot route on it.

[`StoDevX/ole-compass`][oc] can be routed on. It is a 2016 course project whose
`data/` holds a hand-built graph of 228 nodes tracing real sidewalks, indoor
hallways and stairs, plus the node id of each building's entrance. Nothing in
ArcGIS or OpenStreetMap carries the indoor links, the stairs or the doors — on a
campus built down a hillside, knowing which connection is stairs is an
accessibility feature rather than a detail.

Its coordinates are pixels on a bitmap, so they are fitted into WGS84 in two
stages:

1. **Affine fit** from each entrance button to its building's label anchor.
2. **ICP refinement** snapping outdoor nodes onto this repo's scraped walkway
   and road lines, iterated, with the building anchors kept in the fit so it
   cannot drift.

Buildings ole-compass never surveyed are then reached by tracing the scraped
walkways out to them, rather than by a straight line that would cut through
whatever lies between.

## Adapted from course-data-visualization

The two-stage fit is [`StoDevX/course-data-visualization`][cdv]'s
`scripts/build_path_graph.py`, which worked it out first. This differs from it
in three ways, each of which shows in the output:

- **It reads this repo's own data** — `data/buildings.geojson`,
  `data/walkways.geojson`, `data/campus-roads.geojson` and `map.json` — rather
  than copies of those layers kept elsewhere.
- **It covers all 38 buildings.** Keying buildings by this repo's ids (the
  college's abbreviations where they exist, slugified names where they do not)
  keeps New Hall, the Townhouses and Tostrud Center as routable destinations. A
  table keyed on the `ABB` field alone collapses the seven buildings with no
  abbreviation onto one blank key and reaches only 32.
- **It anchors on label anchors, not vertex means.** `geometry.py` guarantees
  those sit inside the building; the mean of a polygon's outer ring does not,
  for anything L-shaped.

[oc]: https://github.com/StoDevX/ole-compass
[cdv]: https://github.com/StoDevX/course-data-visualization
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
VENDOR = ROOT / "vendor" / "ole-compass"

# Metres per degree at St. Olaf's latitude.
M_LAT = 111_132.0
M_LON = 111_320.0 * math.cos(math.radians(44.46))

# ole-compass button label -> this repo's place id. Several buildings have more
# than one door and so appear more than once. These are the 2016 names; the
# right-hand side is what `overrides.yaml` settled on.
NAME_TO_ID = {
    "Mellby": "amh",
    "Rolvaag": "rml",
    "Boe": "bmc",
    "BuntrockSouth": "bc",
    "BuntrockNorth": "bc",
    "TomsonEast": "toh",
    "TomsonWest": "toh",
    "Theater": "tb",
    "HollandN": "hh",
    "HollandS": "hh",
    "RegentsMath": "rms",
    "Larson": "alh",
    "Christiansen": "chm",
    "ChristiansenS": "chm",
    "Skifter": "skh",
    "Hoyme": "hmh",
    "OldMain": "om",
    "Steensland": "sh",
    "Thorson": "th",
    "Ellingson": "eh",
    "Mohn": "mh",
    "Kildahl": "kh",
    "Dittman": "cad",
    "HallOfMusic": "hom",
    "Ytterboe": "ytt",
    "Skoglund": "sac",
    "RandW": "rh",
    "RandE": "rh",
    "RegentsScience": "rns",
    "RegentsMid": "rns",
    "RegentsEast": "rns",
    # ole-compass surveyed Hilleboe and Kittelsby as one building with one door.
    "HillKitt": "ghh",
    # Not a lost building. Alumni Hall's own prose calls it "part of the Clemens
    # V. Granskou Compex [sic], named in the honor of the fifth president", which
    # connects Alumni Hall to the Hall of Music. The Hall of Music already has
    # its own button, so this one is Alumni Hall's way in — and course-data-
    # visualization's table dropped it, which is why AHL had no door there.
    "GranskouTunnel": "ahl",
}
# So the pair above still both resolve to a door.
SHARED_ENTRANCE = {"akh": "ghh"}

# A building further than this from the surveyed network gets a connector traced
# through the scraped walkways, rather than being walked to in a straight line.
ORPHAN_GAP_M = 45.0
# How close a scraped vertex must come to a surveyed node to join the two.
STITCH_M = 25.0
# How far to follow the scraped walkways before giving up on reaching the graph.
CONNECTOR_LIMIT_M = 900.0

EDGE_TYPES = {
    "0": "sidewalk",
    "1": "indoor",
    "2": "stairs",
    "4": "path",
    "5": "scraped walkway",
}
# Edge type for connectors: scraped geometry rather than surveyed path, so a
# router can prefer the surveyed network where both exist.
MAP_EDGE = 5


def parse_graph() -> tuple[dict, dict, dict]:
    lines = (VENDOR / "entireMapCalc.txt").read_text().splitlines()
    count = int(lines[0].strip())

    nodes, indoor = {}, {}
    for line in lines[1 : 1 + count]:
        parts = line.split()
        if len(parts) < 4:
            continue
        nid = int(parts[0])
        nodes[nid] = (float(parts[1]), float(parts[2]))
        indoor[nid] = int(parts[3])

    adjacency = {}
    for line in lines[1 + count :]:
        parts = line.split()
        if len(parts) < 2:
            continue
        nid, degree = int(parts[0]), int(parts[1])
        rest = parts[2:]
        adjacency[nid] = [
            (int(rest[2 * i]), int(rest[2 * i + 1]))
            for i in range(degree)
            if 2 * i + 1 < len(rest)
        ]
    return nodes, indoor, adjacency


def parse_buttons() -> list[tuple[int, str]]:
    lines = (VENDOR / "buttonList.txt").read_text().splitlines()
    count = int(lines[0].strip())
    out = []
    for line in lines[1 : 1 + count]:
        parts = line.split()
        if len(parts) >= 6:
            out.append((int(parts[4]), parts[5]))
    return out


def places() -> dict[str, tuple[float, float]]:
    """Every place id with a label anchor, as (lon, lat).

    `map.json` is latitude-first (Carleton's convention, see build.py); this
    flips it, because everything else here works in GeoJSON order.
    """
    records = json.loads((ROOT / "map.json").read_text())
    return {
        record["id"]: (record["center"][1], record["center"][0])
        for record in records
        if record.get("center")
    }


def _lines_from(filename: str) -> list[list[tuple[float, float]]]:
    collection = json.loads((DATA / filename).read_text())
    paths = []
    for feature in collection.get("features") or []:
        geometry = feature.get("geometry") or {}
        kind = geometry.get("type")
        if kind == "LineString":
            paths.append([tuple(p) for p in geometry["coordinates"]])
        elif kind == "MultiLineString":
            paths.extend([tuple(p) for p in line] for line in geometry["coordinates"])
    return paths


def scraped_paths() -> list[list[tuple[float, float]]]:
    """The lines the fit is snapped to: walkways first, then roads.

    Roads are included because ole-compass surveyed a few nodes along them, and
    because the connectors traced to unsurveyed buildings sometimes have to
    follow a service road to get there.
    """
    return _lines_from("walkways.geojson") + _lines_from("campus-roads.geojson")


def segments_of(paths) -> list[tuple[tuple, tuple]]:
    return [(path[i], path[i + 1]) for path in paths for i in range(len(path) - 1)]


def network_of(paths) -> dict:
    """The scraped lines as an undirected graph of their vertices."""
    neighbours: dict = {}

    def key(point):
        return (round(point[0], 7), round(point[1], 7))

    for path in paths:
        for i in range(len(path) - 1):
            a, b = key(path[i]), key(path[i + 1])
            if a == b:
                continue
            neighbours.setdefault(a, set()).add(b)
            neighbours.setdefault(b, set()).add(a)
    return neighbours


def metres_between(a, b) -> float:
    return math.hypot((a[0] - b[0]) * M_LON, (a[1] - b[1]) * M_LAT)


def solve3(matrix, rhs):
    a = [row[:] for row in matrix]
    b = rhs[:]
    for i in range(3):
        pivot = max(range(i, 3), key=lambda r: abs(a[r][i]))
        a[i], a[pivot] = a[pivot], a[i]
        b[i], b[pivot] = b[pivot], b[i]
        for r in range(i + 1, 3):
            factor = a[r][i] / a[i][i]
            for c in range(i, 3):
                a[r][c] -= factor * a[i][c]
            b[r] -= factor * b[i]
    x = [0.0, 0.0, 0.0]
    for i in (2, 1, 0):
        x[i] = (b[i] - sum(a[i][c] * x[c] for c in range(i + 1, 3))) / a[i][i]
    return x


def fit_affine(src, dst):
    """Least-squares affine mapping pixel (x, y) onto (lon, lat)."""
    sxx = sxy = sx1 = syy = sy1 = s11 = 0.0
    for px, py in src:
        sxx += px * px
        sxy += px * py
        sx1 += px
        syy += py * py
        sy1 += py
        s11 += 1.0
    matrix = [[sxx, sxy, sx1], [sxy, syy, sy1], [sx1, sy1, s11]]

    def rhs(values):
        a = b = c = 0.0
        for (px, py), t in zip(src, values, strict=True):
            a += t * px
            b += t * py
            c += t
        return [a, b, c]

    return (
        solve3(matrix, rhs([d[0] for d in dst])),
        solve3(matrix, rhs([d[1] for d in dst])),
    )


def nearest_on_segments(point, segments):
    """Closest point on any segment, and how far away it is in metres."""
    px, py = point[0] * M_LON, point[1] * M_LAT
    best, best_dist = None, float("inf")
    for (ax, ay), (bx, by) in segments:
        axm, aym = ax * M_LON, ay * M_LAT
        dx, dy = bx * M_LON - axm, by * M_LAT - aym
        length_sq = dx * dx + dy * dy
        t = 0.0
        if length_sq:
            t = max(0.0, min(1.0, ((px - axm) * dx + (py - aym) * dy) / length_sq))
        cx, cy = axm + t * dx, aym + t * dy
        dist = math.hypot(px - cx, py - cy)
        if dist < best_dist:
            best_dist, best = dist, (cx / M_LON, cy / M_LAT)
    return best[0], best[1], best_dist


def connector_to_network(start, network, reached, limit_m):
    """Trace the scraped walkways outward from `start` until they meet the graph.

    Dijkstra over the scraped vertices. Returns the chain of vertices that got
    there, or None if nothing within `limit_m` connects back — in which case the
    building is left off the network rather than joined by a line through the
    middle of a field.
    """
    if start not in network:
        return None

    best = {start: 0.0}
    came_from = {start: None}
    frontier = [(0.0, start)]
    arrival = None

    while frontier:
        frontier.sort()
        cost, current = frontier.pop(0)
        if cost > best.get(current, float("inf")) or cost > limit_m:
            continue
        if reached(current):
            arrival = current
            break
        for neighbour in network[current]:
            step = cost + metres_between(current, neighbour)
            if step < best.get(neighbour, float("inf")) and step <= limit_m:
                best[neighbour] = step
                came_from[neighbour] = current
                frontier.append((step, neighbour))

    if arrival is None:
        return None

    chain = []
    node = arrival
    while node is not None:
        chain.append(node)
        node = came_from[node]
    chain.reverse()
    return chain


def main() -> int:
    nodes, indoor, adjacency = parse_graph()
    buttons = parse_buttons()
    anchors = places()

    # Stage 1: coarse affine from entrance buttons to building label anchors.
    anchor_src, anchor_dst = [], []
    for node_id, name in buttons:
        place_id = NAME_TO_ID.get(name)
        if place_id and place_id in anchors and node_id in nodes:
            anchor_src.append(nodes[node_id])
            anchor_dst.append(anchors[place_id])

    unmapped = sorted({n for _, n in buttons} - set(NAME_TO_ID))
    if unmapped:
        print(f"  buttons with no place id (ignored): {', '.join(unmapped)}")
    if len(anchor_src) < 3:
        print(
            "ERROR: fewer than 3 usable entrance anchors — cannot fit", file=sys.stderr
        )
        return 1

    coeff_lon, coeff_lat = fit_affine(anchor_src, anchor_dst)
    print(f"  affine fit from {len(anchor_src)} building entrances")

    def project(px, py):
        return (
            coeff_lon[0] * px + coeff_lon[1] * py + coeff_lon[2],
            coeff_lat[0] * px + coeff_lat[1] * py + coeff_lat[2],
        )

    # Stage 2: ICP against the scraped walkways.
    paths = scraped_paths()
    segments = segments_of(paths)
    outdoor = [nid for nid, flag in indoor.items() if flag == 0]
    for _ in range(12):
        icp_src, icp_dst = [], []
        for nid in outdoor:
            lon, lat = project(*nodes[nid])
            near_lon, near_lat, dist = nearest_on_segments((lon, lat), segments)
            # Reject far correspondences so tunnels do not drag the fit.
            if dist < 40.0:
                icp_src.append(nodes[nid])
                icp_dst.append((near_lon, near_lat))
        if len(icp_src) < 12:
            break
        # Keep the building anchors in the fit so it cannot drift off them.
        coeff_lon, coeff_lat = fit_affine(icp_src + anchor_src, icp_dst + anchor_dst)

    residuals = sorted(
        nearest_on_segments(project(*nodes[nid]), segments)[2] for nid in outdoor
    )
    median = residuals[len(residuals) // 2]
    print(
        f"  {len(outdoor)} outdoor nodes sit {median:.1f} m from the scraped paths (median)"
    )

    # Emit the georeferenced graph.
    node_ids = sorted(nodes)
    index_of = {nid: i for i, nid in enumerate(node_ids)}
    out_nodes = [[round(c, 7) for c in project(*nodes[nid])] for nid in node_ids]

    edges, seen = [], set()
    self_loops = []
    for nid, neighbours in adjacency.items():
        for other, edge_type in neighbours:
            if nid not in index_of or other not in index_of:
                continue
            if nid == other:
                # Two nodes in the 2016 data list themselves as a neighbour:
                #     121  3  119 0  121 0  123 0
                # A zero-length edge from a node to itself carries no route and
                # inflates the node's degree, so it is dropped rather than
                # emitted. Kept visible here because it is a defect in the
                # source, not in this conversion.
                self_loops.append(nid)
                continue
            key = (min(nid, other), max(nid, other))
            if key in seen:
                continue
            seen.add(key)
            edges.append([index_of[key[0]], index_of[key[1]], edge_type])

    if self_loops:
        print(
            f"  dropped {len(self_loops)} self-loop(s) present in the source "
            f"data: node(s) {', '.join(str(n) for n in sorted(self_loops))}"
        )

    entrances: dict[str, list[int]] = {}
    for node_id, name in buttons:
        place_id = NAME_TO_ID.get(name)
        if place_id and place_id in anchors and node_id in index_of:
            entrances.setdefault(place_id, []).append(index_of[node_id])
    for place_id, shares_with in SHARED_ENTRANCE.items():
        if place_id in anchors and shares_with in entrances:
            entrances.setdefault(place_id, list(entrances[shares_with]))

    # Buildings ole-compass never surveyed sit far enough off its network that
    # walking to them would cut straight across whatever lies between. Trace the
    # scraped walkways out to them so they are reached by a path that exists.
    buildings = {
        record["id"]
        for record in json.loads((ROOT / "map.json").read_text())
        if "building" in record["categories"]
    }
    missing = sorted(buildings - set(entrances))
    network = network_of(paths)
    stitched = []

    for place_id in missing:
        anchor = anchors[place_id]
        nearest_graph = min(
            range(len(out_nodes)), key=lambda i: metres_between(anchor, out_nodes[i])
        )
        gap = metres_between(anchor, out_nodes[nearest_graph])
        if gap <= ORPHAN_GAP_M:
            continue  # close enough that the walk to the door is unremarkable

        start = min(network, key=lambda v: metres_between(anchor, v))
        settled = list(range(len(out_nodes)))

        def meets_graph(vertex, _settled=settled):
            return any(
                metres_between(vertex, out_nodes[i]) <= STITCH_M for i in _settled
            )

        chain = connector_to_network(start, network, meets_graph, CONNECTOR_LIMIT_M)
        if not chain:
            print(
                f"  {place_id}: no route through the scraped walkways, "
                f"left {gap:.0f} m off the network"
            )
            continue

        first = len(out_nodes)
        for vertex in chain:
            out_nodes.append([round(vertex[0], 7), round(vertex[1], 7)])
        for offset in range(len(chain) - 1):
            edges.append([first + offset, first + offset + 1, MAP_EDGE])

        end_vertex = chain[-1]
        join = min(settled, key=lambda i: metres_between(end_vertex, out_nodes[i]))
        edges.append([first + len(chain) - 1, join, MAP_EDGE])
        entrances[place_id] = [first]

        walked = sum(
            metres_between(chain[i], chain[i + 1]) for i in range(len(chain) - 1)
        )
        stitched.append(place_id)
        print(
            f"  {place_id}: was {gap:.0f} m off the network, now reached by "
            f"{len(chain)} points over {walked:.0f} m of scraped walkway"
        )

    still_missing = [p for p in missing if p not in stitched]
    if still_missing:
        print(f"  entering at their nearest node: {', '.join(still_missing)}")

    output = {
        "source": "https://github.com/StoDevX/ole-compass",
        "note": (
            "Pedestrian routing graph. Node coordinates are [longitude, latitude]. "
            "Edges are [nodeA, nodeB, edgeType]. Entrances map this repo's place "
            "ids to node indices. Surveyed in 2016 and georeferenced against this "
            "repo's scraped walkways; see scripts/build_routing.py."
        ),
        "edgeTypes": EDGE_TYPES,
        "nodes": out_nodes,
        "edges": edges,
        "entrances": {k: entrances[k] for k in sorted(entrances)},
    }
    path = ROOT / "routing.json"
    path.write_text(json.dumps(output, indent=1) + "\n")

    by_type: dict[str, float] = {}
    for a, b, kind in edges:
        by_type[EDGE_TYPES.get(str(kind), str(kind))] = by_type.get(
            EDGE_TYPES.get(str(kind), str(kind)), 0.0
        ) + metres_between(out_nodes[a], out_nodes[b])
    print(
        f"\n  {len(out_nodes)} nodes, {len(edges)} edges, "
        f"{len(entrances)} of {len(buildings)} buildings with a door"
    )
    for kind, metres in sorted(by_type.items(), key=lambda kv: -kv[1]):
        print(f"    {kind:<18} {metres / 1000:.2f} km")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
