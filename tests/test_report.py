"""Tests for grouping changes into areas and summarising them."""

from changes import ChangeSet, Move, PlaceChange
from report import areas, release_notes, summary

METRES_PER_DEGREE_LAT = 110_540


def point(lat: float, lon: float = -93.18) -> dict:
    return {"type": "Point", "coordinates": [lon, lat]}


def north_of(lat: float, metres: float) -> float:
    return lat + metres / METRES_PER_DEGREE_LAT


def moved_trail(name: str, lat: float, lon: float = -93.18) -> PlaceChange:
    stretch = ((lon, lat), (lon, lat + 0.0002))
    return PlaceChange(
        "changed",
        name,
        name,
        name,
        move=Move(added=(stretch,), added_m=22.0),
        categories=("trail",),
    )


def added_rack(index: int) -> PlaceChange:
    return PlaceChange(
        "added",
        None,
        f"rack-{index}",
        f"Rack {index}",
        after_shape=point(44.46 + index * 0.00001),
        categories=("bike-rack",),
    )


def changes_of(*places: PlaceChange, routing_changed: bool = False) -> ChangeSet:
    return ChangeSet(tuple(places), (), routing_changed)


def test_moves_near_each_other_share_an_area():
    a = moved_trail("A", 44.46)
    b = moved_trail("B", north_of(44.46, 100) + 0.0002)
    assert len(areas(changes_of(a, b))) == 1


def test_moves_far_apart_get_their_own_areas():
    a = moved_trail("A", 44.46)
    b = moved_trail("B", north_of(44.46, 600))
    assert len(areas(changes_of(a, b))) == 2


def test_a_chain_of_near_moves_is_one_area():
    chain = [moved_trail(str(i), north_of(44.46, i * 140)) for i in range(4)]
    assert len(areas(changes_of(*chain))) == 1


def test_areas_run_north_to_south_then_west_to_east():
    south = moved_trail("South", 44.45)
    north_east = moved_trail("NE", 44.47, -93.17)
    north_west = moved_trail("NW", 44.47, -93.19)
    result = areas(changes_of(south, north_east, north_west))
    assert [[c.name for c in area] for area in result] == [["NW"], ["NE"], ["South"]]


def test_a_text_only_change_has_no_area():
    text = PlaceChange("changed", "a", "a", "A", categories=("building",))
    assert areas(changes_of(text)) == []


def test_a_change_with_an_invisible_move_has_no_area():
    quiet = PlaceChange("changed", "a", "a", "A", move=Move())
    assert areas(changes_of(quiet)) == []


def test_an_added_place_is_placed_by_its_own_shape():
    assert len(areas(changes_of(added_rack(0)))) == 1


def test_a_new_layer_of_points_is_one_area():
    racks = [
        PlaceChange(
            "added",
            None,
            f"rack-{i}",
            f"Rack {i}",
            after_shape=point(44.46 + i * 0.01),
            categories=("bike-rack",),
        )
        for i in range(36)
    ]
    assert len(areas(changes_of(*racks))) == 1


def test_a_few_scattered_added_points_are_not_a_layer():
    racks = [
        PlaceChange(
            "added", None, f"r{i}", f"R{i}", after_shape=point(44.46 + i * 0.01)
        )
        for i in range(3)
    ]
    assert len(areas(changes_of(*racks))) == 3


def trail_changes() -> ChangeSet:
    names = [
        "Lower Heath Creek Trail",
        "Big Woods Trail",
        "Zed Trail",
        "Burr Oak Trail",
    ]
    trails = [moved_trail(name, 44.40 + i * 0.01) for i, name in enumerate(names)]
    racks = [added_rack(i) for i in range(36)]
    return changes_of(*racks, *trails)


def test_summary_counts_and_names_the_places():
    assert summary(trail_changes()) == (
        "36 bike racks added. 4 trails changed shape: "
        "Big Woods Trail, Burr Oak Trail, Lower Heath Creek Trail and 1 more."
    )


def test_summary_uses_the_singular_for_one():
    assert summary(changes_of(added_rack(0))) == "1 bike rack added: Rack 0."


def test_summary_says_what_was_removed_and_renamed():
    gone = PlaceChange("removed", "old", None, "Old Hall", categories=("building",))
    renamed = PlaceChange(
        "renamed", "x", "y", "New Name", categories=("parking",), move=None
    )
    assert summary(changes_of(gone, renamed)) == (
        "1 building removed: Old Hall. 1 parking lot renamed: New Name."
    )


def test_summary_counts_text_only_changes_apart_from_shape_changes():
    text = PlaceChange("changed", "a", "a", "Alpha", categories=("building",))
    assert summary(changes_of(text, moved_trail("Beta Trail", 44.4))) == (
        "1 trail changed shape: Beta Trail. 1 building changed: Alpha."
    )


def test_summary_falls_back_to_the_category_slug():
    odd = PlaceChange("added", None, "a", "Gazebo", categories=("picnic-area",))
    assert summary(changes_of(odd)) == "1 picnic area added: Gazebo."


def test_summary_mentions_rebuilt_routes():
    assert summary(changes_of(added_rack(0), routing_changed=True)).endswith(
        " The walking routes were rebuilt to match."
    )


def test_release_notes_link_the_report_and_commit():
    notes = release_notes(trail_changes(), "https://r", "https://c")
    assert "https://r" in notes and "https://c" in notes
    assert summary(trail_changes()) in notes


def test_release_notes_count_each_kind():
    notes = release_notes(trail_changes(), "https://r", "https://c")
    assert "| Added | 36 |" in notes
    assert "| Changed | 4 |" in notes
    assert "| Removed | 0 |" in notes
