"""verify.py's checks on the assembled trails and the rules overrides."""

from build import RULES_LINK_LABEL
from verify import Report, verify_rules, verify_trails

TRAILS = {
    "supersedes": ["Heath Creek Trail"],
    "trails": [{"name": "Robin Trail", "segments": [1], "miles": 0.1}],
}


def rows(*names):
    return [{"properties": {"NAME": name}} for name in names]


def trail_failures(spec, source_rows):
    report = Report()
    verify_trails(report, spec, source_rows)
    return report.failures


def test_a_superseded_row_that_is_there_passes():
    assert trail_failures(TRAILS, rows("Heath Creek Trail", "Knoll Loop")) == []


# The college renamed it, and the rough line is back as a place.
def test_a_superseded_name_the_source_no_longer_has_fails():
    [message] = trail_failures(TRAILS, rows("Heath Creek Trails", "Knoll Loop"))
    assert "Heath Creek Trail" in message


# Two places of one name number their ids, and Recents loses the trail.
def test_a_source_row_sharing_an_assembled_name_fails():
    [message] = trail_failures(TRAILS, rows("Heath Creek Trail", "Robin Trail"))
    assert "Robin Trail" in message


HREF = "https://example.com/rules/"
BIKES = "Bikes allowed."
RULES = {
    "href": HREF,
    "categories": ["water", "trail"],
    "shared": ["Dogs on a leash.", BIKES],
    "replace": {"trail-norwayvalleytrail": {BIKES: "No bikes."}},
}


def record(id, rules, links, categories=("outdoors", "trail")):
    return {"id": id, "rules": rules, "links": links, "categories": list(categories)}


LINK = {"label": RULES_LINK_LABEL, "href": HREF}
GOOD = [
    record("trail-norwayvalleytrail", ["Dogs on a leash.", "No bikes."], [LINK]),
    record("lot-porter", [], [], categories=["parking"]),
]


def rule_failures(spec, records):
    report = Report()
    verify_rules(report, spec, records)
    return report.failures


def test_rules_as_built_pass():
    assert rule_failures(RULES, GOOD) == []


def test_a_replacement_for_an_unknown_place_fails():
    spec = {**RULES, "replace": {"trail-norwayvalley": {BIKES: "No bikes."}}}
    assert rule_failures(spec, GOOD)


# The shared sentence was reworded and the replacement no longer applies.
def test_a_replacement_of_a_sentence_not_shared_fails():
    spec = {**RULES, "replace": {"trail-norwayvalleytrail": {"Bikes ok.": "No bikes."}}}
    assert rule_failures(spec, GOOD)


def test_a_place_with_rules_needs_the_link_once():
    records = [record("trail-norwayvalleytrail", ["Dogs on a leash.", "No bikes."], [])]
    assert rule_failures(RULES, records)
    records = [record("trail-norwayvalleytrail", ["No bikes."], [LINK, LINK])]
    assert rule_failures(RULES, records)


def test_a_place_in_a_rules_category_without_rules_fails():
    pond = record("pond-bigpond", [], [], categories=["outdoors", "water"])
    assert rule_failures(RULES, [*GOOD, pond]) == ["pond-bigpond: rules missing"]


# The categories come from overrides.yaml: a trail with rules is a failure once
# the spec no longer gives trails the rules.
def test_rules_outside_the_specs_categories_fail():
    assert rule_failures({**RULES, "categories": ["water"]}, GOOD) == [
        "trail-norwayvalleytrail: rules on a place outside the Natural Lands"
    ]


def test_rules_with_no_categories_fail():
    spec = {**RULES, "categories": []}
    assert "overrides.yaml: rules has no categories" in rule_failures(spec, GOOD)
