"""What the scraper keeps of a layer's fields, and what `--check` reports."""

import pytest
import scrape
from scrape import clean_properties
from sources import Source


def test_volatile_fields_are_dropped():
    assert clean_properties({"FID": 7, "NAME": "Knoll Loop", "EditDate": 1}) == {
        "NAME": "Knoll Loop",
        "EditDate": 1,
    }


# A layer whose segments are addressed by FID keeps it, and nothing it does
# not name: its edit stamps would churn the file on every edit.
def test_a_layer_naming_its_fields_keeps_only_those():
    properties = {"FID": 7, "NAME": None, "Type": "Wide", "EditDate": 1, "Creator": "x"}
    assert clean_properties(properties, ("FID", "NAME", "Type")) == {
        "FID": 7,
        "NAME": None,
        "Type": "Wide",
    }


# A named field upstream renamed is missing from the feature, not null in it:
# keeping it as None would blank every FID and quietly unmap the trails.
def test_a_named_field_the_layer_no_longer_has_fails():
    with pytest.raises(ValueError, match="FID"):
        clean_properties({"OBJECTID": 7, "NAME": None, "Type": "Wide"}, ("FID", "NAME"))


LAYER = Source(slug="water", title="Water", url="https://x/0", role="place")
POND = {
    "type": "Feature",
    "properties": {"Name": "Big Pond", "layer": "Water"},
    "geometry": {"type": "Point", "coordinates": [-93.18, 44.46]},
}


@pytest.fixture
def scraping(monkeypatch, tmp_path):
    """Scrape one layer holding one pond into `tmp_path`, with no network."""
    monkeypatch.setattr(scrape, "SOURCES", [LAYER])
    monkeypatch.setattr(scrape, "fetch", lambda source: [POND])
    monkeypatch.setattr(scrape, "DATA", tmp_path)
    return tmp_path


def snapshot(directory):
    return {path.name: path.read_text() for path in sorted(directory.iterdir())}


def test_a_scrape_writes_each_layer_and_the_sources(scraping):
    assert scrape.main([]) == 0
    assert sorted(snapshot(scraping)) == ["sources.json", "water.geojson"]


def test_check_passes_when_the_files_are_current(scraping):
    scrape.main([])
    before = snapshot(scraping)
    assert scrape.main(["--check"]) == 0
    assert snapshot(scraping) == before


def test_check_fails_on_a_difference_and_writes_nothing(scraping, capsys):
    scrape.main([])
    (scraping / "water.geojson").write_text("stale\n")
    before = snapshot(scraping)
    assert scrape.main(["--check"]) == 1
    assert snapshot(scraping) == before
    assert "water.geojson" in capsys.readouterr().err


def test_check_fails_on_a_missing_file(scraping):
    assert scrape.main(["--check"]) == 1
    assert snapshot(scraping) == {}


def test_an_unknown_flag_is_refused_before_any_scrape(scraping):
    with pytest.raises(SystemExit) as refused:
        scrape.main(["--chek"])
    assert refused.value.code == 2
    assert snapshot(scraping) == {}
