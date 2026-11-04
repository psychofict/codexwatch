from datetime import datetime

import pytest

from codexwatch import periods

NOW = datetime(2026, 11, 4, 15, 30).timestamp()


def day(y, m, d):
    return datetime(y, m, d).timestamp()


def test_calendar_periods():
    assert periods.parse("today", NOW)[:2] == (day(2026, 11, 4), None)
    assert periods.parse("yesterday", NOW)[:2] == (day(2026, 11, 3), day(2026, 11, 4))
    assert periods.parse("week", NOW)[0] == day(2026, 11, 2)  # Monday
    assert periods.parse("month", NOW)[0] == day(2026, 11, 1)
    assert periods.parse("year", NOW)[0] == day(2026, 1, 1)
    assert periods.parse("all", NOW)[:2] == (None, None)


def test_rolling_periods():
    assert periods.parse("7d", NOW)[0] == day(2026, 10, 29)  # today and the six days before
    assert periods.parse("5h", NOW)[0] == NOW - 5 * 3600
    assert periods.parse("2w", NOW)[0] == day(2026, 10, 22)


def test_named_months_and_days():
    since, until, label = periods.parse("2026-02", NOW)
    assert (since, until, label) == (day(2026, 2, 1), day(2026, 3, 1), "February 2026")
    assert periods.parse("2026-12", NOW)[1] == day(2027, 1, 1)
    assert periods.parse("2026-10-31", NOW)[:2] == (day(2026, 10, 31), day(2026, 11, 1))


def test_bad_period():
    with pytest.raises(ValueError):
        periods.parse("fortnight", NOW)


def test_dates():
    assert periods.parse_date("2026-11-04") == day(2026, 11, 4)
    assert periods.parse_date("2026-11-04", end=True) == day(2026, 11, 5)
    with pytest.raises(ValueError):
        periods.parse_date("04/11/2026")
