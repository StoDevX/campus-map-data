"""verify.py's checks on the emergency services and the buildings they are in."""

from verify import Report, verify_emergency_services

# Alumni Hall, a square about 80 m on a side, latitude first as in map.json.
OUTLINE = [[44.46, -93.18], [44.46, -93.179], [44.4607, -93.179],
           [44.4607, -93.18], [44.46, -93.18]]  # fmt: skip
INSIDE = [44.4603, -93.1795]
ACROSS_CAMPUS = [44.4650, -93.1795]  # about 480 m north


def building(**fields) -> dict:
    return {
        "id": "ah",
        "name": "Alumni Hall",
        "categories": {"building": True},
        "outline": OUTLINE,
        "parent": None,
        **fields,
    }


def place(kind: str, name: str, parent: str | None, center=INSIDE, **fields) -> dict:
    categories = {"emergency-services": True, kind: True}
    return {
        "id": f"{kind}-1",
        "name": name,
        "categories": categories,
        "center": center,
        "outline": None,
        "parent": parent,
        **fields,
    }


def failures(records: list[dict]) -> list[str]:
    report = Report()
    verify_emergency_services(report, records)
    return report.failures


def test_an_aed_inside_the_building_it_is_named_for_passes():
    records = [building(), place("aed", "AED, Alumni Hall (By the elevator)", "ah")]
    assert failures(records) == []


def test_a_naloxone_kit_inside_its_building_passes():
    name = "Naloxone and Fentanyl Test Strips, Alumni Hall (Front desk)"
    assert failures([building(), place("naloxone", name, "ah")]) == []


def test_an_assembly_point_needs_no_parent():
    assert failures([place("assembly-point", "Assembly Point, North Plaza", None)]) == []


def test_an_aed_inside_no_building_fails():
    [message] = failures([place("aed", "AED, Somewhere - Upstairs", None)])
    assert "overrides.yaml" in message


def test_an_aed_outside_its_parent_fails():
    records = [
        building(),
        place("aed", "AED, Alumni Hall (Upstairs)", "ah", center=ACROSS_CAMPUS),
    ]
    assert any("aed-1" in failure for failure in failures(records))


def test_an_aed_not_named_for_its_parent_fails():
    assert failures([building(), place("aed", "AED, Old Main (Upstairs)", "ah")])


def test_two_emergency_places_of_one_name_fail():
    records = [
        building(),
        place("aed", "AED, Alumni Hall (Upstairs)", "ah"),
        place("aed", "AED, Alumni Hall (Upstairs)", "ah", id="aed-2"),
    ]
    assert any("AED, Alumni Hall (Upstairs)" in f for f in failures(records))
