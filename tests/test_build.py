"""How build.py merges split lots and names the accessible spots."""

from build import feature, merge_split_lots, name_accessible_spots, record

LAT = 44.46


def square(lon: float, lat: float = LAT, size: float = 0.0002) -> dict:
    ring = [[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size],
            [lon, lat]]  # fmt: skip
    return {"type": "Polygon", "coordinates": [ring]}


def lot(name: str, lon: float, **fields) -> dict:
    return {
        "slug": "parking-lots",
        "id": f"lot-{name.lower()}",
        "name": name,
        "description": "Reserved for Faculty/Staff permits.",
        "type": "Campus Parking",
        "categories": ["parking", "campus-parking"],
        "links": [],
        "geometry": square(lon),
        **fields,
    }


def spot(lon: float, lat: float = LAT + 0.0001, **fields) -> dict:
    return {
        "slug": "accessible-parking",
        "id": "accessibleparking-1",
        "name": "Accessible Parking",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        **fields,
    }


def test_pieces_of_one_lot_merge_into_one_place():
    places = merge_split_lots([lot("Porter", -93.180), lot("Porter", -93.1797)])
    assert len(places) == 1
    assert places[0]["geometry"]["type"] == "MultiPolygon"
    assert len(places[0]["geometry"]["coordinates"]) == 2


# A second lot that happens to share a name, across campus, is a different lot.
def test_lots_far_apart_stay_apart_though_they_share_a_name():
    places = merge_split_lots([lot("Lot N", -93.180), lot("Lot N", -93.170)])
    assert len(places) == 2


def test_merging_reports_pieces_that_disagree_about_more_than_prose(capsys):
    merge_split_lots(
        [lot("Porter", -93.180), lot("Porter", -93.1797, type="General Vistor Parking")]
    )
    assert "Porter" in capsys.readouterr().err


def test_a_spot_is_named_for_the_lot_it_sits_in():
    places = [lot("Porter", -93.180), spot(-93.1799)]
    name_accessible_spots(places)
    assert places[1]["name"] == "Accessible Parking, Porter"
    assert places[1]["parent"] == "lot-porter"


# overrides.yaml can say better than the geometry which lot a spot serves.
def test_a_spot_with_a_hand_set_parent_keeps_it():
    places = [
        lot("Porter", -93.180),
        lot("Rand", -93.170),
        spot(-93.1799, parent="lot-rand", name="Accessible Parking, Rand"),
    ]
    name_accessible_spots(places)
    assert places[2]["parent"] == "lot-rand"
    assert places[2]["name"] == "Accessible Parking, Rand"


def test_a_spot_out_of_reach_of_everything_is_left_alone():
    places = [lot("Porter", -93.180), spot(-93.170)]
    name_accessible_spots(places)
    assert "parent" not in places[1]
    assert places[1]["name"] == "Accessible Parking"


def test_a_trail_carries_its_length_in_metres():
    trail = {
        "id": "trail-knollloop",
        "name": "Knoll Loop",
        "categories": ["outdoors", "trail"],
        "geometry": {
            "type": "LineString",
            "coordinates": [[-93.18, 44.46], [-93.18, 44.461]],
        },
    }
    assert record(trail)["length"] == 111
    assert feature(trail)["properties"]["length"] == 111


def test_a_place_with_no_line_has_no_length():
    assert record(lot("Porter", -93.180))["length"] is None

