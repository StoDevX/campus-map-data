"""What the scraper keeps of a layer's fields."""

import pytest
from scrape import clean_properties


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
