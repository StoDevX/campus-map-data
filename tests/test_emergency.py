"""How build.py names the emergency services and finds the buildings they are in."""

from build import (
    assign_ids,
    label_emergency_places,
    name_emergency_places,
    parent_emergency_places,
)

LAT, LON = 44.46, -93.18


def square(lon: float, lat: float = LAT, size: float = 0.0004) -> dict:
    ring = [[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size],
            [lon, lat]]  # fmt: skip
    return {"type": "Polygon", "coordinates": [ring]}


def building(name: str, place_id: str, lon: float = LON) -> dict:
    return {"slug": "buildings", "id": place_id, "name": name, "geometry": square(lon)}


def emergency(kind: str, name: str, lon: float = LON + 0.0002, **fields) -> dict:
    categories = ["emergency-services", kind]
    if kind == "naloxone":
        categories.append("fentanyl-test-strips")
    return {
        "slug": "emergency-services",
        "id": "emergency-1",
        "name": name,
        "categories": categories,
        "geometry": {"type": "Point", "coordinates": [lon, LAT + 0.0002]},
        **fields,
    }


def aed(location: str, **fields) -> dict:
    return emergency("aed", location, location=location, **fields)


def test_each_kind_takes_its_word_into_its_id():
    places = [
        aed("Alumni Hall - First floor, by elevator"),
        emergency("assembly-point", "North Plaza", location="North Plaza"),
        emergency(
            "naloxone",
            "Holland Hall",
            building="Holland Hall",
            location="Third floor in south stairwell",
        ),
    ]
    label_emergency_places(places)
    assign_ids(places, {})
    assert sorted(place["id"] for place in places) == [
        "aedalumnihallfirstfloorbyelevator",
        "assemblypointnorthplaza",
        "naloxonehollandhallthirdfloorinsouthstairwell",
    ]


def test_an_aed_is_parented_on_the_building_it_is_inside():
    places = [
        building("Alumni Hall", "ah"),
        building("Old Main", "om", lon=LON + 0.01),
        aed("Alumni Hall - First floor, by elevator"),
    ]
    parent_emergency_places(places)
    assert places[2]["parent"] == "ah"


# The AED the college labels "(Tweet) Hall of Music" sits where that building
# meets Alumni Hall, inside both footprints.
def test_a_point_inside_two_buildings_takes_the_one_it_is_labelled_for():
    places = [
        building("Alumni Hall", "ah"),
        building("(Tweet) Hall of Music", "hom"),
        aed("(Tweet) Hall of Music - Second floor, outside Music Department office"),
    ]
    parent_emergency_places(places)
    assert places[2]["parent"] == "hom"


def test_an_aed_inside_no_building_has_no_parent():
    places = [building("Alumni Hall", "ah"), aed("Somewhere", lon=LON + 0.01)]
    parent_emergency_places(places)
    assert places[1].get("parent") is None


def test_an_assembly_point_has_no_parent():
    places = [
        building("Alumni Hall", "ah"),
        emergency("assembly-point", "North Plaza", location="North Plaza"),
    ]
    parent_emergency_places(places)
    assert places[1].get("parent") is None


def test_an_aed_is_named_for_its_building_and_where_inside_it_is():
    places = [
        building("Buntrock Commons", "bc"),
        aed("Buntrock Commons (2 of 2) - Third floor, Kings' Dining Room", parent="bc"),
    ]
    name_emergency_places(places)
    assert places[1]["name"] == (
        "AED, Buntrock Commons (Third floor, Kings' Dining Room)"
    )


# Named after the building as published, so a spelling overrides.yaml fixes in
# the building -- the layer's "Theater Buildilng" -- is fixed here too.
def test_naloxone_is_named_for_the_kit_its_building_and_its_spot():
    places = [
        building("Theater Building", "tb"),
        emergency(
            "naloxone",
            "Theater Buildilng",
            building="Theater Buildilng",
            location="Main lobby, Right next to AED",
            parent="tb",
        ),
    ]
    name_emergency_places(places)
    assert places[1]["name"] == (
        "Naloxone and Fentanyl Test Strips, Theater Building "
        "(Main lobby, Right next to AED)"
    )


def test_an_aed_with_no_spot_is_named_for_its_building_alone():
    places = [building("Old Main", "om"), aed("Old Main", parent="om")]
    name_emergency_places(places)
    assert places[1]["name"] == "AED, Old Main"


def test_an_assembly_point_is_named_for_where_it_is():
    places = [emergency("assembly-point", "North Plaza", location="North Plaza")]
    label_emergency_places(places)
    name_emergency_places(places)
    assert places[0]["name"] == "Assembly Point, North Plaza"


# verify.py reports it; a guessed name would hide that.
def test_an_aed_with_no_parent_keeps_its_name():
    places = [aed("Somewhere - Upstairs")]
    label_emergency_places(places)
    name_emergency_places(places)
    assert places[0]["name"] == "AED, Somewhere - Upstairs"
