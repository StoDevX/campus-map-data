"""scripts/watch.py: a page's selected contents, as a snapshot."""

import shutil

import pytest
from watch import run_htmlq, snapshot

SELECT = [
    {"css": "h2.wp-block-heading", "text": True},
    {"css": "a.wp-block-file__button", "attribute": "href"},
]


def fake(matches):
    """An htmlq stand-in: the same page answers each selector from `matches`."""

    def query(html, css, attribute):
        return matches[(css, attribute)]

    return query


PAGE = {
    ("h2.wp-block-heading", None): ["Big Pond Loop: 0.8 miles", "  ", "Norway Valley"],
    ("a.wp-block-file__button", "href"): ["https://x/pond.pdf", "https://x/norway.pdf"],
}


def test_a_snapshot_lists_each_selectors_matches_in_order():
    assert snapshot("", SELECT, fake(PAGE)) == (
        "# h2.wp-block-heading\n"
        "Big Pond Loop: 0.8 miles\n"
        "Norway Valley\n"
        "# a.wp-block-file__button @href\n"
        "https://x/pond.pdf\n"
        "https://x/norway.pdf\n"
    )


def test_a_changed_heading_changes_the_snapshot():
    changed = {
        **PAGE,
        ("h2.wp-block-heading", None): ["Big Pond Loop: 0.9 miles", "Norway Valley"],
    }
    assert snapshot("", SELECT, fake(changed)) != snapshot("", SELECT, fake(PAGE))


# A redesigned page is not a page with no walks.
def test_a_selector_matching_nothing_fails():
    empty = {**PAGE, ("h2.wp-block-heading", None): ["  "]}
    with pytest.raises(SystemExit, match="h2.wp-block-heading"):
        snapshot("", SELECT, fake(empty))


@pytest.mark.skipif(shutil.which("htmlq") is None, reason="htmlq is not on PATH")
def test_htmlq_reads_text_and_attributes():
    html = '<h2 class="a">One</h2><a class="b" href="/one.pdf">Download</a>'
    assert run_htmlq(html, "h2.a", None) == ["One"]
    assert run_htmlq(html, "a.b", "href") == ["/one.pdf"]
