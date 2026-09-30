"""The Natural Lands rules each pond and trail carries."""

from build import RULES_LINK_LABEL, apply_rules

BIKES = "Bikes allowed, except where signs say no bikes."
SPEC = {
    "href": "https://wp.stolaf.edu/naturallands/visitor-information-and-rules/",
    "categories": ["water", "trail"],
    "shared": ["Dogs on a 6-foot leash.", BIKES],
    "replace": {"trail-norwayvalleytrail": {BIKES: "No bikes."}},
}


def place(id: str, categories: list[str]) -> dict:
    return {"id": id, "categories": categories, "links": []}


def test_a_trail_gets_the_shared_rules_and_the_link():
    trail = place("trail-knollloop", ["outdoors", "trail"])
    apply_rules([trail], SPEC)
    assert trail["rules"] == SPEC["shared"]
    assert trail["links"] == [{"label": RULES_LINK_LABEL, "href": SPEC["href"]}]


def test_a_replacement_swaps_one_rule():
    trail = place("trail-norwayvalleytrail", ["outdoors", "trail"])
    apply_rules([trail], SPEC)
    assert trail["rules"] == ["Dogs on a 6-foot leash.", "No bikes."]


def test_a_field_gets_no_rules():
    field = place("field-mabelshirleyfield", ["athletics", "outdoors"])
    apply_rules([field], SPEC)
    assert field["rules"] == []
    assert field["links"] == []


def test_applying_twice_adds_the_link_once():
    pond = place("pond-bigpond", ["outdoors", "water"])
    apply_rules([pond], SPEC)
    apply_rules([pond], SPEC)
    assert len(pond["links"]) == 1
