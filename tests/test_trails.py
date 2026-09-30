"""Building a trail from the college's segments."""

import pytest
from build import assemble_trails


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
