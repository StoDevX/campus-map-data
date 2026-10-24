"""How build.py names the bike racks for the places they serve."""

from build import assign_ids, name_bike_racks, parent_bike_racks

LAT = 44.46


def place(slug: str, name: str, id: str) -> dict:
    return {"slug": slug, "name": name, "id": id}


def rack(name: str, side: str | None, **fields) -> dict:
    return {
        "slug": "bike-racks",
        "id": "bike-rack-1",
        "name": name,
        "side": side,
        "geometry": {"type": "Point", "coordinates": [-93.18, LAT]},
        **fields,
    }


def test_a_rack_is_parented_on_the_building_it_is_named_for():
    places = [
        place("buildings", "Holland Hall", "hh"),
        rack("Holland Hall", "North", id="bike-rack-hollandhallnorth"),
    ]
    parent_bike_racks(places)
    assert places[1]["parent"] == "hh"


# Mabel Shirley Field has a rack, and is an athletic field, not a building.
def test_a_rack_is_parented_on_the_field_it_is_named_for():
    places = [
        place("athletic-fields", "Mabel Shirley Field", "field-mabelshirleyfield"),
        rack("Mabel Shirley Field", "East", id="bike-rack-mabelshirleyfieldeast"),
    ]
    parent_bike_racks(places)
    assert places[1]["parent"] == "field-mabelshirleyfield"


# The lot beside Old Main is called "Old Main" too; a rack serves the building.
def test_a_rack_is_not_parented_on_a_lot_of_the_same_name():
    places = [
        place("parking-lots", "Old Main", "lot-oldmain"),
        place("buildings", "Old Main", "om"),
        rack("Old Main", "West", id="bike-rack-oldmainwest"),
    ]
    parent_bike_racks(places)
    assert places[2]["parent"] == "om"


def test_a_rack_named_for_nothing_has_no_parent():
    places = [
        place("buildings", "Rolvaag Memorial Library", "rml"),
        rack("St. Olaf Ave + Rolvaag Memorial Library", None, id="bike-rack-x"),
    ]
    parent_bike_racks(places)
    assert places[1].get("parent") is None


def test_racks_on_two_sides_of_a_building_get_ids_by_side():
    places = [rack("Holland Hall", "North"), rack("Holland Hall", "South")]
    assign_ids(places, {})
    assert [p["id"] for p in places] == [
        "bike-rack-hollandhallnorth",
        "bike-rack-hollandhallsouth",
    ]


# Named after the parent's name as published, so a spelling the overrides fix
# in the building is fixed in its racks too.
def test_a_rack_is_named_for_its_parent_and_side():
    places = [
        place("buildings", "(Gertrude) Hilleboe Hall", "ghh"),
        rack("(Gertrude) Hillboe Hall", "East", parent="ghh"),
    ]
    name_bike_racks(places)
    assert places[1]["name"] == "Bike Rack, (Gertrude) Hilleboe Hall (East)"


def test_a_rack_with_no_side_is_named_for_its_parent_alone():
    places = [
        place("buildings", "Rolvaag Memorial Library", "rml"),
        rack("St. Olaf Ave + Rolvaag Memorial Library", None, parent="rml"),
    ]
    name_bike_racks(places)
    assert places[1]["name"] == "Bike Rack, Rolvaag Memorial Library"


# verify.py reports it; a guessed name would hide that.
def test_a_rack_with_no_parent_keeps_its_name():
    places = [rack("St. Olaf Ave + Rolvaag Memorial Library", None, parent=None)]
    name_bike_racks(places)
    assert places[0]["name"] == "St. Olaf Ave + Rolvaag Memorial Library"
