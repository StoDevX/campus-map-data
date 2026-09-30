"""verify.py's checks on the citations behind a place's About text."""

from verify import Report, verify_citations

CITATION = {"label": "History of the Natural Lands", "href": "https://example.com/h/"}


def place(citations, description="The oldest forest on campus."):
    return {"id": "trail-x", "description": description, "citations": citations}


def failures(records):
    report = Report()
    verify_citations(report, records)
    return report.failures


def test_a_cited_description_passes():
    assert failures([place([CITATION])]) == []


def test_a_place_with_no_citations_passes():
    assert failures([place([], description=None)]) == []


def test_a_citation_without_a_label_fails():
    assert failures([place([{**CITATION, "label": " "}])]) == [
        "trail-x: a citation has no label"
    ]


def test_a_citation_not_over_https_fails():
    assert failures([place([{**CITATION, "href": "http://example.com/h/"}])]) == [
        "trail-x: citation 'History of the Natural Lands' does not link over https"
    ]


def test_citations_without_a_description_fail():
    assert failures([place([CITATION], description="")]) == [
        "trail-x: has citations but no description"
    ]
