"""verify.py's checks on parking lots and the accessible spots named for them."""

from verify import Report, verify_parking

OUTLINE = [[44.46, -93.18], [44.46, -93.179], [44.461, -93.179], [44.46, -93.18]]


def lot(name: str, **fields) -> dict:
    return {
        "id": f"lot-{name.lower()}",
        "name": name,
        "categories": {"parking": True, "campus-parking": True},
        "outline": OUTLINE,
        "parent": None,
        **fields,
    }


def spot(number: int, name: str, parent: str | None) -> dict:
    return {
        "id": f"accessibleparking-{number}",
        "name": name,
        "categories": {"parking": True, "accessible-parking": True},
        "outline": None,
        "parent": parent,
    }


def failures(records: list[dict]) -> list[str]:
    report = Report()
    verify_parking(report, records)
    return report.failures


def test_spots_named_for_their_lots_pass():
    assert (
        failures([lot("Porter"), spot(1, "Accessible Parking, Porter", "lot-porter")])
        == []
    )


# build.py merges scraped lots of one name, so a repeat can only come from a
# rename or an addition in overrides.yaml, and the message should say so.
def test_two_lots_of_one_name_point_at_the_overrides():
    [message] = failures([lot("Porter"), lot("Porter", id="lot-porter-2")])
    assert "overrides.yaml" in message


def test_two_spots_of_one_name_fail():
    records = [
        lot("Porter"),
        spot(1, "Accessible Parking, Porter", "lot-porter"),
        spot(2, "Accessible Parking, Porter", "lot-porter"),
    ]
    assert any("Accessible Parking, Porter" in f for f in failures(records))


# Two spots in one lot are told apart by hand, in overrides.yaml.
def test_a_spot_may_add_to_its_lots_name():
    records = [
        lot("Porter"),
        spot(1, "Accessible Parking, Porter (north)", "lot-porter"),
        spot(2, "Accessible Parking, Porter (south)", "lot-porter"),
    ]
    assert failures(records) == []


def test_a_spot_whose_parent_has_no_footprint_fails():
    records = [
        lot("Porter", outline=None),
        spot(1, "Accessible Parking, Porter", "lot-porter"),
    ]
    assert failures(records)


def test_a_spot_with_no_parent_fails():
    assert failures([spot(1, "Accessible Parking", None)])
