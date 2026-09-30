"""Building a trail from the college's segments."""

import pytest
from build import assemble_trails, drop_superseded


def segment(fid: int, coordinates: list) -> dict:
    return {
        "type": "Feature",
        "properties": {"FID": fid, "NAME": None, "Type": "Wide"},
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }


NORTH = segment(1, [[-93.18, 44.460], [-93.18, 44.461]])
# Runs on south past the junction at 44.462.
LONG = segment(2, [[-93.18, 44.461], [-93.18, 44.462], [-93.18, 44.463]])
JUNCTION = [-93.18, 44.46201]  # beside the vertex at 44.462


def test_a_trail_joins_its_segments_in_order():
    [trail] = assemble_trails(
        [LONG, NORTH],
        {"trails": [{"name": "Robin Trail", "segments": [1, 2], "miles": 0.21}]},
    )
    assert trail["name"] == "Robin Trail"
    assert trail["categories"] == ["outdoors", "trail"]
    assert trail["geometry"]["coordinates"] == [
        NORTH["geometry"]["coordinates"],
        LONG["geometry"]["coordinates"],
    ]


def test_a_cut_gives_each_trail_its_side_of_a_shared_vertex():
    big_woods, burr_oak = assemble_trails(
        [NORTH, LONG],
        {
            "trails": [
                {
                    "name": "Big Woods Trail",
                    "segments": [1, {"fid": 2, "until": JUNCTION}],
                    "miles": 0.14,
                },
                {
                    "name": "Burr Oak Trail",
                    "segments": [{"fid": 2, "from": JUNCTION}],
                    "miles": 0.07,
                },
            ]
        },
    )
    assert big_woods["geometry"]["coordinates"][-1] == [
        [-93.18, 44.461],
        [-93.18, 44.462],
    ]
    assert burr_oak["geometry"]["coordinates"][0] == [
        [-93.18, 44.462],
        [-93.18, 44.463],
    ]


def test_a_missing_segment_fails_the_build():
    with pytest.raises(SystemExit, match="FID 9"):
        assemble_trails(
            [NORTH],
            {"trails": [{"name": "Robin Trail", "segments": [1, 9], "miles": 0.07}]},
        )


# The college re-cut its segments: the mapping is stale, and a person redoes it.
def test_a_trail_whose_length_moved_fails_the_build():
    with pytest.raises(SystemExit, match="Robin Trail"):
        assemble_trails(
            [NORTH],
            {"trails": [{"name": "Robin Trail", "segments": [1], "miles": 0.5}]},
        )


def test_no_trails_listed_builds_none():
    assert assemble_trails([NORTH], {}) == []


def test_a_segment_drawn_in_pieces_fails_the_build():
    pieces = {
        **NORTH,
        "geometry": {
            "type": "MultiLineString",
            "coordinates": [NORTH["geometry"]["coordinates"]],
        },
    }
    with pytest.raises(SystemExit, match="FID 1 is drawn in pieces"):
        assemble_trails(
            [pieces],
            {"trails": [{"name": "Robin Trail", "segments": [1], "miles": 0.07}]},
        )


def test_a_segment_the_layer_holds_twice_fails_the_build():
    with pytest.raises(SystemExit, match="FID 1 is in the segments layer twice"):
        assemble_trails(
            [NORTH, NORTH],
            {"trails": [{"name": "Robin Trail", "segments": [1], "miles": 0.07}]},
        )


def test_a_cut_away_from_every_vertex_fails_the_build():
    # 0.0005 degrees of latitude short of 44.462: about 55 m from any vertex.
    with pytest.raises(SystemExit, match="no vertex within 5 m"):
        assemble_trails(
            [LONG],
            {
                "trails": [
                    {
                        "name": "Robin Trail",
                        "segments": [{"fid": 2, "until": [-93.18, 44.4615]}],
                        "miles": 0.07,
                    }
                ]
            },
        )


# A wrong FID whose length happens to match: the length guard misses it, but
# the segment lies nowhere near the rest of the trail.
FAR = segment(3, [[-93.17, 44.460], [-93.17, 44.461]])


def test_a_segment_that_touches_nothing_else_in_its_trail_fails_the_build():
    with pytest.raises(SystemExit, match="FID 3 touches no other part"):
        assemble_trails(
            [NORTH, LONG, FAR],
            {"trails": [{"name": "Robin Trail", "segments": [1, 2, 3], "miles": 0.28}]},
        )


# Ends partway along LONG, between its vertices, as trails meet at a T.
TEE = segment(4, [[-93.179, 44.4615], [-93.18, 44.4615]])


def test_a_segment_meeting_another_partway_along_it_touches_it():
    [trail] = assemble_trails(
        [LONG, TEE],
        {"trails": [{"name": "Robin Trail", "segments": [2, 4], "miles": 0.19}]},
    )
    assert len(trail["geometry"]["coordinates"]) == 2


def test_a_superseded_source_trail_is_dropped_and_nothing_else():
    def place(slug, name):
        return {"slug": slug, "name": name}

    places = [
        place("natural-lands-trails", "Heath Creek Trail"),
        place("natural-lands-trails", "Big Pond Loop"),
        # The same name in another layer is some other place.
        place("water", "Heath Creek Trail"),
    ]
    kept = drop_superseded(places, {"supersedes": ["Heath Creek Trail"]})
    assert kept == places[1:]


def test_no_supersedes_drops_nothing():
    places = [{"slug": "natural-lands-trails", "name": "Big Pond Loop"}]
    assert drop_superseded(places, {}) == places
