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


def setup_watch(tmp_path, monkeypatch, heading):
    import watch

    (tmp_path / "watches.yaml").write_text(
        "- name: walks\n"
        "  url: https://example.com/walks\n"
        "  select:\n"
        "    - {css: h2}\n"
        "  why: Update `walks:`.\n"
    )
    snapshots = tmp_path / "data" / "watches"
    snapshots.mkdir(parents=True)
    (snapshots / "walks.txt").write_text("# h2\nBig Pond Loop\n")
    monkeypatch.setattr(watch, "ROOT", tmp_path)
    monkeypatch.setattr(watch, "SNAPSHOTS", snapshots)
    monkeypatch.setattr(watch, "fetch", lambda url: "")
    monkeypatch.setattr(watch, "run_htmlq", lambda html, css, attribute: [heading])
    return watch, snapshots / "walks.txt"


def test_check_reports_a_change_and_writes_nothing(tmp_path, monkeypatch):
    watch, path = setup_watch(tmp_path, monkeypatch, "Windmill Trail")
    monkeypatch.setattr("sys.argv", ["watch.py", "--check"])
    assert watch.main() == 1
    assert path.read_text() == "# h2\nBig Pond Loop\n"


def test_check_passes_when_nothing_changed(tmp_path, monkeypatch):
    watch, _ = setup_watch(tmp_path, monkeypatch, "Big Pond Loop")
    monkeypatch.setattr("sys.argv", ["watch.py", "--check"])
    assert watch.main() == 0


# The workflow's pull request says what each changed watch asks of us.
def test_a_run_writes_the_snapshot_and_a_report_of_why(tmp_path, monkeypatch):
    watch, path = setup_watch(tmp_path, monkeypatch, "Windmill Trail")
    report = tmp_path / "report.md"
    monkeypatch.setattr("sys.argv", ["watch.py"])
    monkeypatch.setenv("WATCH_REPORT", str(report))
    assert watch.main() == 0
    assert path.read_text() == "# h2\nWindmill Trail\n"
    assert "walks" in report.read_text()
    assert "Update `walks:`." in report.read_text()
