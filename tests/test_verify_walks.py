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
    assert "overrides.yaml" in message and "https://x/new.pdf" in message


def test_no_snapshot_yet_skips_the_count():
    assert failures(GOOD, None) == []


# The college retires one walk and publishes another: the count is unchanged.
def test_a_walk_swapped_for_another_fails():
    swapped = SNAPSHOT.replace("https://x/pond.pdf", "https://x/new.pdf")
    messages = failures(GOOD, swapped)
    assert any("https://x/new.pdf" in m for m in messages)
    assert any("https://x/pond.pdf" in m for m in messages)


def test_a_guide_the_page_links_twice_counts_once():
    assert failures(GOOD, SNAPSHOT + "https://x/pond.pdf\n") == []


# Another selector after the guides must not be read as more guides.
def test_a_section_after_the_guides_is_not_counted():
    more = SNAPSHOT + "# p.note\nSomething else\n"
    assert failures(GOOD, more) == []
