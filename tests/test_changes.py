"""Tests for reading a state of the published files from git or the working tree."""

import json
import subprocess
from pathlib import Path

import pytest
from changes import (
    MOVE_REACH_M,
    FieldChange,
    State,
    compare,
    moved,
    read_state,
    word_diff,
)

POLYGON = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]],
}
LABEL = {"type": "Point", "coordinates": [0.5, 0.5]}


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def write_json(root: Path, path: str, data) -> None:
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(json.dumps(data))


def feature(place_id: str, geometries: list[dict]) -> dict:
    return {
        "type": "Feature",
        "id": place_id,
        "geometry": {"type": "GeometryCollection", "geometries": geometries},
        "properties": {},
    }


def make_repo(root: Path, name: str = "Old Main", *, walkways: int = 2) -> None:
    git(root, "init", "-q")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    write_json(root, "map.json", [{"id": "om", "name": name}])
    write_json(
        root,
        "map.geojson",
        {
            "type": "FeatureCollection",
            "features": [
                feature("om", [POLYGON, LABEL]),
                feature("bike-rack-x", [LABEL]),
            ],
        },
    )
    write_json(
        root,
        "data/walkways.geojson",
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": POLYGON, "properties": {}}
                for _ in range(walkways)
            ],
        },
    )
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "init")


def test_a_revision_reads_the_files_as_committed(tmp_path):
    make_repo(tmp_path)
    write_json(tmp_path, "map.json", [{"id": "om", "name": "Old Main Hall"}])
    assert read_state(tmp_path, "HEAD").places["om"]["name"] == "Old Main"
    assert read_state(tmp_path, None).places["om"]["name"] == "Old Main Hall"


def test_a_place_shape_is_its_geometry_not_its_label_point(tmp_path):
    make_repo(tmp_path)
    assert read_state(tmp_path, None).shapes["om"]["type"] == "Polygon"


def test_a_point_place_shape_is_its_point(tmp_path):
    make_repo(tmp_path)
    assert read_state(tmp_path, None).shapes["bike-rack-x"]["type"] == "Point"


def test_context_holds_the_context_layers(tmp_path):
    make_repo(tmp_path)
    assert len(read_state(tmp_path, None).context["walkways"]) == 2


def test_a_file_missing_at_a_revision_reads_as_empty(tmp_path):
    make_repo(tmp_path)
    state = read_state(tmp_path, "HEAD")
    assert state.context["campus-roads"] == []
    assert state.routing == b""


def test_an_unknown_revision_is_an_error_not_an_empty_state(tmp_path):
    make_repo(tmp_path)
    with pytest.raises(ValueError, match="no-such-revision"):
        read_state(tmp_path, "no-such-revision")


def test_routing_is_read_when_present(tmp_path):
    make_repo(tmp_path)
    (tmp_path / "routing.json").write_bytes(b'{"a": 1}')
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "routing")
    assert read_state(tmp_path, "HEAD").routing == b'{"a": 1}'
    assert read_state(tmp_path, None).routing == b'{"a": 1}'


def place(place_id: str, lat: float = 44.0, lon: float = -93.0, **extra) -> dict:
    return {
        "id": place_id,
        "name": place_id,
        "center": [lat, lon],
        "categories": {"building": True},
        **extra,
    }


def state_of(*places: dict, context=None, routing: bytes = b"") -> State:
    return State(
        places={p["id"]: p for p in places},
        shapes={},
        context=context or {},
        routing=routing,
    )


def test_identical_states_have_no_changes():
    state = state_of(place("a"))
    assert compare(state, state).is_empty


def test_a_new_id_is_added():
    changes = compare(state_of(), state_of(place("bike-rack-x")))
    (change,) = changes.places
    assert change.kind == "added"
    assert (change.before_id, change.after_id) == (None, "bike-rack-x")


def test_a_missing_id_is_removed():
    changes = compare(state_of(place("a")), state_of())
    (change,) = changes.places
    assert change.kind == "removed"
    assert (change.before_id, change.after_id) == ("a", None)


def test_a_changed_field_is_listed_before_and_after():
    changes = compare(
        state_of(place("a", description="A")),
        state_of(place("a", description="B")),
    )
    (change,) = changes.places
    assert change.kind == "changed"
    assert change.fields == (FieldChange("description", "A", "B"),)


def test_a_moved_center_is_not_a_field_change():
    changes = compare(state_of(place("a")), state_of(place("a", lat=44.1)))
    assert all(c.field != "center" for p in changes.places for c in p.fields)


# Ids derive from names, so a rename arrives as one removed and one added.
def rename_pair(lat_after: float, categories_after=None):
    after = place("hillboehall", lat=lat_after)
    if categories_after:
        after["categories"] = categories_after
    return compare(state_of(place("hilleboehall")), state_of(after))


def test_a_removed_and_added_place_side_by_side_are_a_rename():
    # 0.00009 degrees of latitude is about 10 m
    changes = rename_pair(44.00009)
    assert [c.kind for c in changes.places] == ["renamed"]
    (change,) = changes.places
    assert (change.before_id, change.after_id) == ("hilleboehall", "hillboehall")


def test_places_far_apart_are_not_a_rename():
    changes = rename_pair(44.0018)  # about 200 m
    assert [c.kind for c in changes.places] == ["added", "removed"]


def test_places_of_different_categories_are_not_a_rename():
    changes = rename_pair(44.00009, {"parking": True})
    assert [c.kind for c in changes.places] == ["added", "removed"]


def test_a_context_line_that_appears_is_added():
    line = {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
    changes = compare(
        state_of(context={"walkways": []}),
        state_of(context={"walkways": [line]}),
    )
    (change,) = changes.context
    assert change.slug == "walkways"
    assert change.added == (line,)
    assert change.removed == ()


def test_only_routing_changed_is_empty():
    changes = compare(state_of(routing=b"1"), state_of(routing=b"2"))
    assert changes.routing_changed and changes.is_empty


def test_comparing_twice_gives_the_same_order():
    before = state_of(place("a"), place("b", description="x"))
    after = state_of(place("c"), place("b", description="y"), place("d"))
    assert compare(before, after) == compare(before, after)


def line_at(lat: float, east: float = -93.0001) -> dict:
    return {"type": "LineString", "coordinates": [[-93.0, lat], [east + 0.0002, lat]]}


POINT = {"type": "Point", "coordinates": [-93.0, 44.0]}


def square_at(lat: float) -> dict:
    d = 0.0001
    ring = [[-93, lat], [-93 + d, lat], [-93 + d, lat + d], [-93, lat + d], [-93, lat]]
    return {"type": "Polygon", "coordinates": [ring]}


def test_a_line_redrawn_13_m_away_is_dropped_and_added_over_its_8_m_length():
    move = moved(line_at(44.4600), line_at(44.46012))
    assert move.visible
    assert round(move.dropped_m) == round(move.added_m) == 8


def big_square(inset: float = 0.0) -> dict:
    """An ~80 m square, pulled in by `inset` degrees on every side."""
    lo, hi = inset, 0.001 - inset
    ring = [[-93 + lo, 44 + lo], [-93 + hi, 44 + lo], [-93 + hi, 44 + hi]]
    ring += [[-93 + lo, 44 + hi], [-93 + lo, 44 + lo]]
    return {"type": "Polygon", "coordinates": [ring]}


def test_a_line_nudged_under_3_m_is_not_a_move():
    assert not moved(line_at(44.4600), line_at(44.46001)).visible


def along(*lons, lat=44.0):
    return {"type": "LineString", "coordinates": [[lon, lat] for lon in lons]}


def test_a_shortened_line_drops_only_its_end():
    move = moved(along(-93.0, -93.0015), along(-93.0, -93.00075))  # ~120 m to ~60 m
    # The cut end is within reach of the kept half, so ~3 m of it is not counted.
    assert abs(move.dropped_m - (60 - MOVE_REACH_M)) < 1
    assert move.added_m == 0 and not move.added
    assert len(move.dropped) == 1


def test_a_stretch_deleted_from_the_middle_is_measured():
    before = along(-93.0, -93.0015)  # ~120 m
    after = {
        "type": "MultiLineString",
        "coordinates": [
            [[-93.0, 44.0], [-93.0004, 44.0]],
            [[-93.0011, 44.0], [-93.0015, 44.0]],
        ],
    }
    move = moved(before, after)
    assert abs(move.dropped_m - 50) < 3  # the 56 m gap less 3 m of reach each side
    assert not move.added


def test_a_square_redrawn_inside_its_old_outline_shows_both_edges():
    move = moved(big_square(), big_square(inset=0.00013))  # ~10 m in
    assert move.dropped and move.added


def test_a_point_that_became_a_polygon_is_a_move():
    move = moved(POINT, square_at(44.01))
    assert move.visible
    assert move.dropped and move.added


def test_a_point_that_stayed_is_not_a_move():
    assert not moved(POINT, POINT).visible


def test_a_shape_that_appeared_is_all_added():
    move = moved(None, POINT)
    assert move.added and not move.dropped


def test_a_shape_that_vanished_is_all_dropped():
    move = moved(square_at(44.0), None)
    assert move.dropped and not move.added


def test_word_diff_marks_the_changed_words():
    assert word_diff("a red barn", "a blue barn") == (
        ("same", "a"),
        ("removed", "red"),
        ("added", "blue"),
        ("same", "barn"),
    )


def test_a_changed_place_carries_its_move():
    before = place("a", description="A")
    after = place("a", description="B")
    old = State({"a": before}, {"a": line_at(44.0)}, {}, b"")
    new = State({"a": after}, {"a": line_at(44.001)}, {}, b"")
    (change,) = compare(old, new).places
    assert change.move.visible
    assert [f.field for f in change.fields] == ["description", "geometry"]


def test_identical_shapes_add_no_geometry_field_or_move():
    shape = {"a": line_at(44.0)}
    old = State({"a": place("a", description="A")}, shape, {}, b"")
    new = State({"a": place("a", description="B")}, shape, {}, b"")
    (change,) = compare(old, new).places
    assert change.move is None
    assert [f.field for f in change.fields] == ["description"]


def test_a_changed_place_carries_its_categories():
    old = State({"a": place("a", description="A")}, {}, {}, b"")
    new = State({"a": place("a", description="B")}, {}, {}, b"")
    (change,) = compare(old, new).places
    assert change.categories == ("building",)


def test_a_removed_place_carries_the_categories_it_had():
    old = State({"a": place("a")}, {}, {}, b"")
    new = State({}, {}, {}, b"")
    (change,) = compare(old, new).places
    assert change.categories == ("building",)
