"""Geometry helpers: label anchors, bounding boxes, coordinate normalisation.

The one non-obvious job here is picking a **label anchor** — the single point
that represents a place. Carleton's dataset has these hand-placed, which is
exactly what `carls-app/map-data`'s `overrides.yaml` exists to maintain. St.
Olaf's ArcGIS layers have no such point, so one is derived and `overrides.yaml`
can correct any that land badly.

The naive choice, an area-weighted centroid, is wrong for a real campus: it
falls *outside* the shape for anything L-shaped or horseshoe-shaped, which on
this campus means Old Main, the Ade Christenson Complex and most of the parking
lots that wrap a building. A label anchored outside its own building points at
the wrong thing. So the centroid is used when it lands inside the polygon, and
otherwise a guaranteed-interior point is computed instead.

Everything works in lon/lat degrees. Over a 1 km campus the distortion from not
projecting first is far below the precision anyone can place a label to by hand.
"""

from __future__ import annotations

import itertools

# ~1.1 cm at this latitude. Enough that no two distinct places collide, small
# enough that float formatting noise cannot produce a spurious diff in the
# committed data.
PRECISION = 7

Position = list[float]
Ring = list[Position]


def round_coords(value):
    """Recursively round every coordinate in a GeoJSON coordinate structure."""
    if isinstance(value, list):
        return [round_coords(item) for item in value]
    if isinstance(value, float):
        return round(value, PRECISION)
    return value


def polygons(geometry: dict | None) -> list[list[Ring]]:
    """Every polygon in a geometry, each as a list of rings (outer, then holes)."""
    if not geometry:
        return []
    kind = geometry.get("type")
    if kind == "Polygon":
        return [geometry["coordinates"]]
    if kind == "MultiPolygon":
        return list(geometry["coordinates"])
    if kind == "GeometryCollection":
        return [
            polygon
            for member in geometry.get("geometries", [])
            for polygon in polygons(member)
        ]
    return []


def positions(geometry: dict | None) -> list[Position]:
    """Every coordinate pair in a geometry, flattened."""
    if not geometry:
        return []
    if geometry.get("type") == "GeometryCollection":
        return [
            position
            for member in geometry.get("geometries", [])
            for position in positions(member)
        ]

    found: list[Position] = []

    def walk(node) -> None:
        if (
            isinstance(node, list)
            and len(node) >= 2
            and all(isinstance(item, int | float) for item in node[:2])
        ):
            found.append([float(node[0]), float(node[1])])
            return
        if isinstance(node, list):
            for item in node:
                walk(item)

    walk(geometry.get("coordinates"))
    return found


def bbox(geometry: dict | None) -> list[float] | None:
    points = positions(geometry)
    if not points:
        return None
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    return [min(lons), min(lats), max(lons), max(lats)]


def ring_area(ring: Ring) -> float:
    """Signed shoelace area. Sign is orientation; magnitude is what we use."""
    total = 0.0
    for index in range(len(ring) - 1):
        x0, y0 = ring[index][0], ring[index][1]
        x1, y1 = ring[index + 1][0], ring[index + 1][1]
        total += x0 * y1 - x1 * y0
    return total / 2.0


def ring_centroid(ring: Ring) -> tuple[float, float, float]:
    """Area-weighted centroid of one ring, with its signed area."""
    area = ring_area(ring)
    if abs(area) < 1e-15:
        # Degenerate ring (zero area, or a single repeated point). Fall back to
        # the mean of its vertices so it still contributes something sane.
        count = max(len(ring) - 1, 1)
        return (
            sum(point[0] for point in ring[:count]) / count,
            sum(point[1] for point in ring[:count]) / count,
            0.0,
        )

    cx = cy = 0.0
    for index in range(len(ring) - 1):
        x0, y0 = ring[index][0], ring[index][1]
        x1, y1 = ring[index + 1][0], ring[index + 1][1]
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    return (cx / (6.0 * area), cy / (6.0 * area), area)


def point_in_ring(point: Position, ring: Ring) -> bool:
    """Ray-casting test. Points exactly on an edge are not guaranteed either way."""
    x, y = point[0], point[1]
    inside = False
    for index in range(len(ring) - 1):
        x0, y0 = ring[index][0], ring[index][1]
        x1, y1 = ring[index + 1][0], ring[index + 1][1]
        if (y0 > y) != (y1 > y):
            crossing = x0 + (y - y0) / (y1 - y0) * (x1 - x0)
            if crossing > x:
                inside = not inside
    return inside


def point_in_polygon(point: Position, rings: list[Ring]) -> bool:
    if not rings or not point_in_ring(point, rings[0]):
        return False
    # Inside the outer ring, but a hole cancels it.
    return not any(point_in_ring(point, hole) for hole in rings[1:])


def _interior_point(rings: list[Ring], y: float) -> Position | None:
    """Midpoint of the widest span of polygon interior along the line at `y`.

    Every edge crossing that horizontal line is collected, the crossings are
    sorted, and the widest gap whose midpoint is genuinely inside the polygon
    wins. With holes present the crossings do not simply alternate
    inside/outside, so each candidate is tested rather than assumed.
    """
    crossings: list[float] = []
    for ring in rings:
        for index in range(len(ring) - 1):
            x0, y0 = ring[index][0], ring[index][1]
            x1, y1 = ring[index + 1][0], ring[index + 1][1]
            if (y0 > y) != (y1 > y):
                crossings.append(x0 + (y - y0) / (y1 - y0) * (x1 - x0))

    crossings.sort()
    best: Position | None = None
    best_width = 0.0
    for left, right in itertools.pairwise(crossings):
        width = right - left
        if width <= best_width:
            continue
        candidate = [(left + right) / 2.0, y]
        if point_in_polygon(candidate, rings):
            best, best_width = candidate, width
    return best


def label_anchor(geometry: dict | None) -> Position | None:
    """A representative point for a geometry, guaranteed inside it if it is areal.

    For points, the point. For lines, the midpoint vertex. For polygons, the
    centroid if it lands inside, otherwise an interior point on the centroid's
    latitude — and failing that, on a few other latitudes, which covers the
    shapes whose centroid row happens to miss the polygon entirely.
    """
    if not geometry:
        return None

    shapes = polygons(geometry)
    if not shapes:
        points = positions(geometry)
        if not points:
            return None
        if geometry.get("type") in ("Point", "MultiPoint"):
            return points[0]
        # A line: the vertex nearest the middle beats the mean, which for a
        # curved path can sit well off the line itself.
        return points[len(points) // 2]

    # Across a MultiPolygon, the largest part carries the label.
    largest = max(shapes, key=lambda rings: abs(ring_area(rings[0])))
    x, y, _ = ring_centroid(largest[0])
    if point_in_polygon([x, y], largest):
        return [x, y]

    lats = [point[1] for point in largest[0]]
    low, high = min(lats), max(lats)
    # The centroid's own latitude first, then a sweep. Ten rows is ample for
    # building and parking-lot outlines, and costs nothing at this data size.
    candidates = [y] + [low + (high - low) * (index + 0.5) / 10 for index in range(10)]
    for candidate_y in candidates:
        found = _interior_point(largest, candidate_y)
        if found:
            return found

    # Nothing worked, which means the ring is degenerate. The centroid is still
    # a better answer than nothing.
    return [x, y]
