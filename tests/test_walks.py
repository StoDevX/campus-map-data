"""The Wellness Walks: tagged trails and walks of their own."""

import pytest
from build import WALK_LINK_LABEL, apply_walks, walk_places

EAST = [[-93.186, 44.469], [-93.192, 44.470]]
WEST = [[-93.192, 44.470], [-93.196, 44.466]]
PRAIRIE = {
    "slug": "natural-lands-trails",
    "id": "trail-prairieloop",
    "name": "Prairie Loop",
    "categories": ["outdoors", "trail"],
    "links": [],
    "geometry": {"type": "MultiLineString", "coordinates": [EAST, WEST]},
}
POND = {
    "slug": "natural-lands-trails",
    "id": "trail-bigpondloop",
    "name": "Big Pond Loop",
    "categories": ["outdoors", "trail"],
    "links": [{"label": "Natural Lands rules", "href": "https://example.com/rules"}],
    "geometry": {"type": "LineString", "coordinates": EAST},
}
SPEC = {
    "walks": [
        {"trail": "trail-bigpondloop", "minutes": [14, 17], "pdf": "https://x/pond.pdf", "accessibility": "Flat."},
        {"name": "East Prairie Loop", "part": {"trail": "Prairie Loop", "index": 0},
         "minutes": [12, 15], "pdf": "https://x/east.pdf", "accessibility": "Mowed."},
    ]
}  # fmt: skip


def fresh(place):
    return {
        **place,
        "categories": list(place["categories"]),
        "links": list(place["links"]),
    }


def test_a_walk_along_part_of_a_trail_is_a_place_of_its_own():
    [east] = walk_places([fresh(PRAIRIE)], SPEC)
    assert east["name"] == "East Prairie Loop"
    assert east["categories"] == ["outdoors", "trail", "wellness-walk"]
    assert east["geometry"] == {"type": "LineString", "coordinates": EAST}


def test_a_part_walk_on_a_missing_trail_fails():
    with pytest.raises(SystemExit, match="East Prairie Loop"):
        walk_places([], SPEC)


def test_a_part_past_the_trails_parts_fails():
    spec = {
        "walks": [{**SPEC["walks"][1], "part": {"trail": "Prairie Loop", "index": 2}}]
    }
    with pytest.raises(SystemExit, match="East Prairie Loop"):
        walk_places([fresh(PRAIRIE)], spec)


def test_a_walk_along_a_whole_trail_tags_it():
    pond = fresh(POND)
    apply_walks([pond], {"walks": [SPEC["walks"][0]]})
    assert pond["walk"] == {"minutes": [14, 17], "accessibility": "Flat."}
    assert "wellness-walk" in pond["categories"]
    assert {"label": WALK_LINK_LABEL, "href": "https://x/pond.pdf"} in pond["links"]
    # The rules link stays.
    assert pond["links"][0]["label"] == "Natural Lands rules"


def test_applying_twice_adds_the_guide_once():
    pond = fresh(POND)
    apply_walks([pond], {"walks": [SPEC["walks"][0]]})
    apply_walks([pond], {"walks": [SPEC["walks"][0]]})
    assert [link["label"] for link in pond["links"]].count(WALK_LINK_LABEL) == 1
    assert pond["categories"].count("wellness-walk") == 1


def test_a_walk_naming_a_missing_trail_fails():
    with pytest.raises(SystemExit, match="trail-bigpondloop"):
        apply_walks([fresh(PRAIRIE)], {"walks": [SPEC["walks"][0]]})


def test_every_other_place_has_no_walk():
    prairie = fresh(PRAIRIE)
    apply_walks([prairie, fresh(POND)], {"walks": [SPEC["walks"][0]]})
    assert prairie["walk"] is None


def test_a_negative_part_fails():
    spec = {
        "walks": [{**SPEC["walks"][1], "part": {"trail": "Prairie Loop", "index": -1}}]
    }
    with pytest.raises(SystemExit, match="East Prairie Loop"):
        walk_places([fresh(PRAIRIE)], spec)


# A typo that names a car park would put a walk's guide on it.
def test_a_walk_on_a_place_that_is_not_a_trail_fails():
    lot = {**fresh(POND), "id": "lot-porter", "categories": ["parking"]}
    spec = {"walks": [{**SPEC["walks"][0], "trail": "lot-porter"}]}
    with pytest.raises(SystemExit, match="lot-porter"):
        apply_walks([lot], spec)


# A part-walk named like a place that already has its id would share it.
def test_a_part_walk_whose_id_is_taken_fails():
    from build import part_walk_ids_free

    with pytest.raises(SystemExit, match="trail-eastprairieloop"):
        part_walk_ids_free(
            [{"id": "trail-eastprairieloop"}], [{"id": "trail-eastprairieloop"}]
        )
