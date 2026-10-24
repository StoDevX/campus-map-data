"""Tests for naming a release when several changes land on one day."""

from release_name import next_name, release_name


def test_a_free_date_is_its_own_name():
    assert next_name("2026-11-01", set()) == "2026-11-01"


def test_a_second_change_that_day_gets_2():
    assert next_name("2026-11-01", {"2026-11-01"}) == "2026-11-01-2"


def test_a_third_gets_3():
    assert next_name("2026-11-01", {"2026-11-01", "2026-11-01-2"}) == "2026-11-01-3"


def test_other_days_do_not_count():
    assert next_name("2026-11-02", {"2026-11-01", "2026-11-01-2"}) == "2026-11-02"


def test_a_commit_already_released_keeps_its_name():
    assert release_name("2026-11-02", {"2026-11-01"}, {"2026-11-01"}) == "2026-11-01"


def test_a_commit_not_yet_released_gets_the_next_name():
    assert release_name("2026-11-01", {"2026-11-01"}, set()) == "2026-11-01-2"
