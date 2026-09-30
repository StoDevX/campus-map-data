"""Which trail lines the tiles draw, and when they refuse to build."""

import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "campus_layers", Path(__file__).parent.parent / "scripts" / "campus-layers.py"
)
campus_layers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(campus_layers)

ROUGH = [[-93.19, 44.45], [-93.189, 44.451]]
KNOLL = [[-93.18, 44.46], [-93.18, 44.461]]


def write(tmp_path, rows):
    features = [
        {
            "type": "Feature",
            "properties": {"NAME": name},
            "geometry": {"type": "LineString", "coordinates": line},
        }
        for name, line in rows
    ]
    (tmp_path / "natural-lands-trails.geojson").write_text(
        json.dumps({"features": features})
    )
    (tmp_path / "walkways.geojson").write_text(json.dumps({"features": []}))


def trail(id: str, line) -> dict:
    return {
        "id": id,
        "properties": {"name": id, "categories": ["outdoors", "trail"]},
        "geometry": {
            "type": "GeometryCollection",
            "geometries": [{"type": "LineString", "coordinates": line}],
        },
    }


def test_a_superseded_line_is_not_drawn(tmp_path):
    write(tmp_path, [("Heath Creek Trail", ROUGH), ("Knoll Loop", KNOLL)])
    paths = campus_layers.paths(
        str(tmp_path), [trail("trail-knollloop", KNOLL)], {"Heath Creek Trail"}, set()
    )
    drawn = [line for f in paths for line in f["geometry"]["coordinates"]]
    assert ROUGH not in drawn


# A trail built from another layer matches nothing in data/, and is not a
# double draw; a build that rewrote every line's coordinates still fails.
def test_an_assembled_trail_does_not_trip_the_guard(tmp_path):
    write(tmp_path, [("Knoll Loop", KNOLL)])
    campus_layers.paths(
        str(tmp_path),
        [trail("trail-knollloop", KNOLL), trail("trail-robintrail", ROUGH)],
        set(),
        {"trail-robintrail"},
    )


def test_rewritten_coordinates_still_fail(tmp_path):
    write(tmp_path, [("Knoll Loop", KNOLL)])
    moved = [[x + 1e-8, y] for x, y in KNOLL]
    with pytest.raises(SystemExit):
        campus_layers.paths(
            str(tmp_path), [trail("trail-knollloop", moved)], set(), set()
        )


# One trail rewritten while the rest still match is still a double draw.
def test_one_rewritten_trail_among_matching_ones_fails(tmp_path):
    other = [[-93.17, 44.46], [-93.17, 44.461]]
    write(tmp_path, [("Knoll Loop", KNOLL), ("Conifer Trail", other)])
    moved = [[x + 1e-8, y] for x, y in KNOLL]
    with pytest.raises(SystemExit):
        campus_layers.paths(
            str(tmp_path),
            [trail("trail-knollloop", moved), trail("trail-conifertrail", other)],
            set(),
            set(),
        )
