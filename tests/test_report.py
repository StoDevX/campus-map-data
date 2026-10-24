"""Tests for grouping changes into areas, summarising them and the page."""

import os
import re
from pathlib import Path

from changes import ChangeSet, Move, PlaceChange, State, compare
from geometry import METRES_PER_DEGREE_LAT, positions
from report import areas, main, release_notes, summary
from report_page import render
from test_changes import make_repo, write_json


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


def test_moves_near_each_other_east_to_west_share_an_area():
    # 0.0017 degrees of longitude is about 135 m at 44.46 N.
    a = moved_trail("A", 44.46, -93.18)
    b = moved_trail("B", 44.46, -93.18 + 0.0017)
    c = moved_trail("C", 44.46, -93.18 + 0.0017 + 0.0021)  # about 167 m on
    assert [len(area) for area in areas(changes_of(a, b, c))] == [2, 1]


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


def test_summary_names_a_place_by_its_known_category_first():
    trail = PlaceChange(
        "changed", "a", "a", "Robin Trail", categories=("outdoors", "trail")
    )
    assert summary(changes_of(trail)) == "1 trail changed: Robin Trail."


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


# The page. The fixtures below are before and after states run through the real
# `compare`, so the page is tested on what the pipeline actually produces.

GOLDEN = Path(__file__).parent / "golden" / "report.html"
DATE = "2026-11-01"
METRES_PER_DEGREE_LON = 79_000  # near enough at 44.46 N for building fixtures


def east_of(lon: float, metres: float) -> float:
    return lon + metres / METRES_PER_DEGREE_LON


def record(place_id: str, name: str, shape: dict, *categories: str, **extra) -> dict:
    """A `map.json` record whose center is the first position of its shape."""
    lon, lat = positions(shape)[0]
    return {
        "id": place_id,
        "name": name,
        "center": [lat, lon],
        "categories": dict.fromkeys(categories, True),
        **extra,
    }


def square(lat: float, lon: float, metres: float = 30) -> dict:
    top, right = north_of(lat, metres), east_of(lon, metres)
    ring = [[lon, lat], [right, lat], [right, top], [lon, top], [lon, lat]]
    return {"type": "Polygon", "coordinates": [ring]}


def line(*points: tuple[float, float]) -> dict:
    return {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in points]}


def state(
    records: list[dict],
    context: dict[str, list[dict]] | None = None,
    routing: bytes = b"",
) -> State:
    places = {r["id"]: {k: v for k, v in r.items() if k != "shape"} for r in records}
    shapes = {r["id"]: r["shape"] for r in records}
    return State(places, shapes, context or {}, routing)


def place_record(place_id, name, shape, *categories, **extra) -> dict:
    return {**record(place_id, name, shape, *categories, **extra), "shape": shape}


LAT, LON = 44.4600, -93.1800
WATER = square(north_of(LAT, 40), east_of(LON, 260), 60)
WALKWAY = line((LAT, east_of(LON, -40)), (LAT, east_of(LON, 300)))
NEW_WALKWAY = line((north_of(LAT, 80), LON), (north_of(LAT, 80), east_of(LON, 200)))
RIDGE_BEFORE = line(
    (north_of(LAT, 100), LON),
    (north_of(LAT, 100), east_of(LON, 100)),
    (north_of(LAT, 100), east_of(LON, 200)),
)
# The middle stretch now bends 40 m north, and the trail runs 60 m further east.
RIDGE_AFTER = line(
    (north_of(LAT, 100), LON),
    (north_of(LAT, 100), east_of(LON, 60)),
    (north_of(LAT, 140), east_of(LON, 100)),
    (north_of(LAT, 100), east_of(LON, 140)),
    (north_of(LAT, 100), east_of(LON, 260)),
)


def fixture() -> tuple[ChangeSet, State, State]:
    """One of each kind of change, as small as the page can show them."""
    hall = square(LAT + 0.0003, LON)
    lot = square(LAT - 0.0004, east_of(LON, 120), 40)
    shared = [
        place_record("hall", "Hall", hall, "building"),
    ]
    before = state(
        [
            *shared,
            place_record("ridge", "Ridge Trail", RIDGE_BEFORE, "trail", length=200),
            place_record(
                "om",
                "Old Main",
                square(LAT - 0.0008, LON),
                "building",
                description="Built in 1877 of local limestone.",
            ),
            place_record("shed", "Grounds Shed", square(LAT, east_of(LON, -300), 10)),
            place_record("lot-a", "Lot A", lot, "parking"),
        ],
        {"water": [WATER], "walkways": [WALKWAY]},
        b"routes 1",
    )
    after = state(
        [
            *shared,
            place_record("ridge", "Ridge Trail", RIDGE_AFTER, "trail", length=284),
            place_record(
                "om",
                "Old Main",
                square(LAT - 0.0008, LON),
                "building",
                description='Built in 1877 of <b>local</b> & "Kasota" limestone.',
            ),
            place_record(
                "bike-rack-hall",
                "Bike Rack, Hall (North)",
                point(north_of(LAT + 0.0003, 40), east_of(LON, 10)),
                "bike-rack",
            ),
            place_record("lot-b", "Lot B", lot, "parking"),
        ],
        {"water": [WATER], "walkways": [WALKWAY, NEW_WALKWAY]},
        b"routes 2",
    )
    return compare(before, after), before, after


def page_of(before: State, after: State) -> str:
    return render(compare(before, after), before, after, DATE)


def changes_with_description(text: str) -> tuple[State, State]:
    shape = square(LAT, LON)
    return (
        state([place_record("om", "Old Main", shape, "building", description="")]),
        state([place_record("om", "Old Main", shape, "building", description=text)]),
    )


def trails_far_apart(count: int) -> tuple[State, State]:
    """`count` trails 600 m apart, each redrawn 10 m to the east."""
    befores, afters = [], []
    for i in range(count):
        lat = north_of(LAT, i * 600)
        ends = ((lat, LON), (north_of(lat, 100), LON))
        moved_ends = [(la, east_of(lo, 10)) for la, lo in ends]
        befores.append(place_record(f"t{i:02}", f"Trail {i}", line(*ends), "trail"))
        afters.append(
            place_record(f"t{i:02}", f"Trail {i}", line(*moved_ends), "trail")
        )
    return state(befores), state(afters)


def every_building_nudged(metres: float) -> tuple[State, State]:
    """A campus of 60 buildings 200 m apart, every one redrawn `metres` east."""
    befores, afters = [], []
    for i in range(60):
        lat, lon = north_of(LAT, (i // 6) * 200), east_of(LON, (i % 6) * 200)
        befores.append(place_record(f"b{i:02}", f"B{i}", square(lat, lon), "building"))
        afters.append(
            place_record(
                f"b{i:02}", f"B{i}", square(lat, east_of(lon, metres)), "building"
            )
        )
    context = {"walkways": [WALKWAY], "water": [WATER]}
    return state(befores, context), state(afters, context)


def test_rendering_twice_is_byte_identical():
    changes, before, after = fixture()
    assert render(changes, before, after, DATE) == render(changes, before, after, DATE)


def test_the_page_matches_the_golden_file():
    page = render(*fixture(), DATE)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(page)
    assert page == GOLDEN.read_text()


def test_the_title_carries_the_date():
    assert "<title>Campus data changes, 2026-11-01</title>" in render(*fixture(), DATE)


def test_markup_in_the_colleges_text_is_escaped():
    page = page_of(*changes_with_description('<b>Fish & "Chips"</b>'))
    assert "<b>Fish" not in page and "&lt;b&gt;Fish &amp;" in page


def test_a_text_only_change_draws_no_closeup():
    assert 'class="closeup"' not in page_of(*changes_with_description("New words."))


def test_close_ups_stop_at_twelve():
    page = page_of(*trails_far_apart(15))
    assert page.count('class="closeup"') == 12 and "and 3 more areas" in page


def test_a_sweeping_change_stays_under_two_megabytes():
    assert len(page_of(*every_building_nudged(4)).encode()) < 2_000_000


def test_the_page_makes_no_requests_but_fonts():
    page = render(*fixture(), DATE)
    loaded = re.findall(
        r'<(?:link|script|img|iframe)\b[^>]*?(?:src|href)="(https?://[^/"]+)', page
    )
    assert set(loaded) <= {"https://fonts.googleapis.com"}


def test_cards_and_shapes_share_a_place_attribute():
    page = render(*fixture(), DATE)
    assert page.count('data-place="ridge"') >= 2
    assert '<article class="card" data-place="ridge"' in page


def test_a_geometry_change_under_three_metres_is_said_not_hidden():
    before, after = every_building_nudged(1)
    page = page_of(before, after)
    assert 'class="closeup"' not in page and "(under 3 m)" in page


def test_a_redrawn_trail_says_how_much_was_dropped_and_added():
    page = render(*fixture(), DATE)
    assert "m dropped" in page and "m added" in page
    assert "200 m → 284 m" in page


def test_list_fields_show_the_items_added_and_removed():
    shape = square(LAT, LON)
    before = state([place_record("om", "Old Main", shape, "building", floors=["1"])])
    after = state(
        [place_record("om", "Old Main", shape, "building", "academic", floors=["2"])]
    )
    page = page_of(before, after)
    assert '<li class="ins">academic</li>' in page
    assert '<li class="ins">2</li>' in page and '<li class="del">1</li>' in page


def test_context_and_routing_changes_are_noted():
    page = render(*fixture(), DATE)
    assert "Walkways layer: 1 shape added." in page
    assert "Walking directions were rebuilt" in page


def test_svg_coordinates_have_one_decimal():
    page = render(*fixture(), DATE)
    numbers = re.findall(r'd="M([^"]*)"', page)
    assert numbers and not re.search(r"\d\.\d\d", " ".join(numbers))


def test_one_area_draws_no_closeup_because_the_overview_frames_it():
    page = page_of(*trails_far_apart(1))
    assert 'class="closeup"' not in page and 'class="overview"' in page
    assert "close-up" not in page.split('class="overview"')[1].split("</figure>")[0]


def test_two_areas_draw_two_closeups():
    page = page_of(*trails_far_apart(2))
    assert page.count('class="closeup"') == 2
    assert "each has a close-up below" in page


def test_past_twelve_areas_the_overview_says_which_have_close_ups():
    page = page_of(*trails_far_apart(15))
    assert "the first 12 have close-ups below" in page


def test_a_small_redraw_says_nothing_moved_more_than_three_metres():
    assert "no part moved more than 3 m" in page_of(*every_building_nudged(1))


def test_a_background_layer_change_is_described_not_said_to_be_drawn():
    page = render(*fixture(), DATE)
    assert "Walkways layer: 1 shape added." in page and "drawn behind" not in page


def run_report(root: Path, *extra: str) -> int:
    return main(
        ["--root", str(root), "--before", "HEAD", "--date", "2026-10-23", *extra]
    )


def test_a_change_writes_the_page_and_notes(tmp_path, capsys):
    make_repo(tmp_path)
    write_json(tmp_path, "map.json", [{"id": "om", "name": "Old Main Hall"}])
    out, notes = tmp_path / "report.html", tmp_path / "notes.md"
    code = run_report(
        tmp_path,
        "--out",
        str(out),
        "--notes",
        str(notes),
        "--report-url",
        "https://example.com/report",
        "--commit-url",
        "https://example.com/commit",
    )
    assert code == 0
    assert "Campus data changes, 2026-10-23" in out.read_text()
    assert "https://example.com/report" in notes.read_text()
    assert "Old Main" in capsys.readouterr().out


def test_the_page_and_notes_go_into_directories_that_do_not_exist_yet(tmp_path):
    make_repo(tmp_path)
    write_json(tmp_path, "map.json", [{"id": "om", "name": "Old Main Hall"}])
    out = tmp_path / "site" / "changes" / "report.html"
    notes = tmp_path / "tmp" / "notes.md"
    assert run_report(tmp_path, "--out", str(out), "--notes", str(notes)) == 0
    assert out.is_file() and notes.is_file()


def test_no_change_writes_nothing(tmp_path, capsys):
    make_repo(tmp_path)
    out = tmp_path / "report.html"
    assert run_report(tmp_path, "--out", str(out)) == 0
    assert not out.exists()
    assert "No change to the published data." in capsys.readouterr().out


def test_a_bad_revision_fails(tmp_path, capsys):
    make_repo(tmp_path)
    out = tmp_path / "report.html"
    code = main(
        [
            "--root",
            str(tmp_path),
            "--before",
            "nope",
            "--date",
            "2026-10-23",
            "--out",
            str(out),
        ]
    )
    assert code != 0
    assert not out.exists()
    assert "nope" in capsys.readouterr().err
