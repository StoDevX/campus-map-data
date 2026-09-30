"""`distance_m`, which decides which lot or building names an accessible spot."""

import math

import pytest
from geometry import distance_m

# A square about 111 m a side near campus, with a square hole in the middle.
LAT = 44.46
DLON = 0.001 / math.cos(math.radians(LAT)) * (110_540 / 111_320)
SQUARE = [[-93.18, LAT], [-93.18 + DLON, LAT], [-93.18 + DLON, LAT + 0.001],
          [-93.18, LAT + 0.001], [-93.18, LAT]]  # fmt: skip
HOLE = [[-93.18 + DLON * 0.4, LAT + 0.0004], [-93.18 + DLON * 0.6, LAT + 0.0004],
        [-93.18 + DLON * 0.6, LAT + 0.0006], [-93.18 + DLON * 0.4, LAT + 0.0006],
        [-93.18 + DLON * 0.4, LAT + 0.0004]]  # fmt: skip
LOT = {"type": "Polygon", "coordinates": [SQUARE]}
COURTYARD = {"type": "Polygon", "coordinates": [SQUARE, HOLE]}


def test_a_point_inside_an_area_is_no_distance_from_it():
    assert distance_m([-93.18 + DLON / 2, LAT + 0.0005], LOT) == 0


def test_a_point_outside_is_measured_to_the_nearest_edge():
    # 0.0001 degrees of latitude south of the bottom edge: about 11 m.
    point = [-93.18 + DLON / 2, LAT - 0.0001]
    assert distance_m(point, LOT) == pytest.approx(11.05, abs=0.1)


def test_a_point_past_a_corner_is_measured_to_the_corner():
    point = [-93.18 - DLON * 0.1, LAT - 0.0001]
    assert distance_m(point, LOT) == pytest.approx(math.hypot(11.05, 11.05), abs=0.2)


def test_a_point_in_a_hole_is_outside_the_area():
    # The hole's middle is 0.0001 degrees, about 11 m, from its nearest edge.
    point = [-93.18 + DLON / 2, LAT + 0.0005]
    assert distance_m(point, COURTYARD) == pytest.approx(11.05, abs=0.2)


def test_a_multipolygon_is_measured_to_its_nearest_part():
    far = [[x + DLON * 5, y] for x, y in SQUARE]
    both = {"type": "MultiPolygon", "coordinates": [[far], [SQUARE]]}
    assert distance_m([-93.18 + DLON / 2, LAT - 0.0001], both) == pytest.approx(
        11.05, abs=0.1
    )


def test_a_point_geometry_is_measured_to_the_point():
    point = {"type": "Point", "coordinates": [-93.18, LAT]}
    assert distance_m([-93.18, LAT - 0.0001], point) == pytest.approx(11.05, abs=0.1)


def test_nothing_is_infinitely_far():
    assert distance_m([-93.18, LAT], None) == math.inf
