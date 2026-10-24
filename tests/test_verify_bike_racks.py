"""verify.py's checks on the bike racks and the places they are named for."""

from verify import Report, verify_bike_racks

# Holland Hall, a square about 80 m on a side, latitude first as in map.json.
OUTLINE = [[44.46, -93.18], [44.46, -93.179], [44.4607, -93.179],
           [44.4607, -93.18], [44.46, -93.18]]  # fmt: skip
BESIDE = [44.4608, -93.1795]  # about 10 m north of it
ACROSS_CAMPUS = [44.4650, -93.1795]  # about 480 m north


def building(**fields) -> dict:
    return {
        "id": "hh",
        "name": "Holland Hall",
        "categories": {"building": True},
        "outline": OUTLINE,
        "parent": None,
        **fields,
    }


def rack(name: str, parent: str | None, center=BESIDE, **fields) -> dict:
    return {
        "id": "bike-rack-hollandhallnorth",
        "name": name,
        "categories": {"bike-rack": True},
        "center": center,
        "outline": None,
        "parent": parent,
        **fields,
    }


def failures(records: list[dict]) -> list[str]:
    report = Report()
    verify_bike_racks(report, records)
    return report.failures


def test_a_rack_beside_the_building_it_is_named_for_passes():
    assert failures([building(), rack("Bike Rack, Holland Hall (North)", "hh")]) == []


def test_a_rack_with_no_parent_fails():
    [message] = failures([rack("St. Olaf Ave + Rolvaag Memorial Library", None)])
    assert "overrides.yaml" in message


def test_a_rack_whose_parent_has_no_footprint_fails():
    assert failures(
        [building(outline=None), rack("Bike Rack, Holland Hall (North)", "hh")]
    )


# How a mislabelled rack shows: the college named one beside Rand Hall for
# New Hall, across campus.
def test_a_rack_far_from_its_parent_fails():
    [message] = failures(
        [
            building(),
            rack("Bike Rack, Holland Hall (North)", "hh", center=ACROSS_CAMPUS),
        ]
    )
    assert "bike-rack-hollandhallnorth" in message
    assert "overrides.yaml" in message


def test_a_rack_not_named_for_its_parent_fails():
    assert failures([building(), rack("Bike Rack, Old Main (West)", "hh")])


def test_two_racks_of_one_name_fail():
    records = [
        building(),
        rack("Bike Rack, Holland Hall (North)", "hh"),
        rack("Bike Rack, Holland Hall (North)", "hh", id="bike-rack-2"),
    ]
    assert any("Bike Rack, Holland Hall (North)" in f for f in failures(records))


# The rack on St. Olaf Avenue is 53 m from Rolvaag Memorial Library, nearer
# than to anything else, and named for it.
def test_a_rack_across_a_street_from_its_parent_passes():
    across_the_street = [44.4607 + 0.00048, -93.1795]
    assert (
        failures(
            [
                building(),
                rack("Bike Rack, Holland Hall (North)", "hh", center=across_the_street),
            ]
        )
        == []
    )
