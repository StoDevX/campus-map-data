"""verify.py's checks on the Wellness Walks."""

from build import WALK_LINK_LABEL
from verify import Report, verify_walks

GUIDE = {"label": WALK_LINK_LABEL, "href": "https://x/pond.pdf"}
SPEC = {"walks": [{"trail": "trail-bigpondloop", "pdf": "https://x/pond.pdf"}]}
SNAPSHOT = "# h2.wp-block-heading\nBig Pond Loop\n# a.wp-block-file__button @href\nhttps://x/pond.pdf\n"


def record(id, walk, categories, links):
    return {
        "id": id,
        "walk": walk,
        "categories": {c: True for c in categories},
        "links": links,
    }


WALK = {"minutes": [14, 17], "accessibility": "Flat."}
GOOD = [
    record("trail-bigpondloop", WALK, ["trail", "wellness-walk"], [GUIDE]),
    record("lot-porter", None, ["parking"], []),
]


def failures(records, snapshot=SNAPSHOT, spec=SPEC):
    report = Report()
    verify_walks(report, spec, records, snapshot)
    return report.failures


def test_walks_as_built_pass():
    assert failures(GOOD) == []


def test_a_walk_place_without_its_guide_fails():
    assert failures([record("trail-bigpondloop", WALK, ["trail", "wellness-walk"], [])])


def test_a_walk_on_a_place_outside_the_category_fails():
    assert failures([record("lot-porter", WALK, ["parking"], [GUIDE])])


# The page gained a walk; overrides.yaml has not caught up.
def test_more_guides_on_the_page_than_walks_fails():
    more = SNAPSHOT + "https://x/new.pdf\n"
    [message] = failures(GOOD, more)
    assert "overrides.yaml" in message


def test_no_snapshot_yet_skips_the_count():
    assert failures(GOOD, None) == []
