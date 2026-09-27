"""Opening hours: parse free text, validate structured input, Kampala open/closed."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from tenants.opening_hours import (
    opening_status,
    parse_free_text,
    public_hours,
)
from tenants.schemas import ShopCreate, ShopUpdate

KAMPALA = ZoneInfo("Africa/Kampala")


def _at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=KAMPALA)


def test_weekdays_text_parses_and_reports_open_state():
    hours = parse_free_text("Weekdays 9am to 6pm, Saturday mornings")
    assert hours["legacy_text"] == "Weekdays 9am to 6pm, Saturday mornings"
    assert hours["days"]["mon"]["ranges"] == [{"open": "09:00", "close": "18:00"}]
    assert hours["days"]["sat"]["ranges"] == [{"open": "08:00", "close": "12:00"}]
    assert hours["days"]["sun"]["closed"] is True

    wed_morning = _at(2026, 1, 7, 10)  # Wednesday
    assert opening_status(hours, wed_morning) == (True, "Closes 18:00")
    wed_evening = _at(2026, 1, 7, 19)
    assert opening_status(hours, wed_evening) == (False, "Opens Thu 09:00")
    saturday = _at(2026, 1, 10, 13)
    assert opening_status(hours, saturday) == (False, "Opens Mon 09:00")


def test_overnight_range_and_24_hours():
    hours = ShopCreate(
        name="Night",
        slug="night",
        availability={
            "days": {
                "fri": {"closed": False, "ranges": [{"open": "22:00", "close": "02:00"}]},
            }
        },
    ).availability
    assert opening_status(hours, _at(2026, 1, 9, 23)) == (True, "Closes Sat 02:00")
    assert opening_status(hours, _at(2026, 1, 10, 1)) == (True, "Closes 02:00")

    always = ShopUpdate(availability={"open_24_hours": True, "note": "Counter is staffed"}).availability
    assert opening_status(always, _at(2026, 1, 7, 3)) == (True, "Open 24 hours")


def test_legacy_blob_stays_readable_when_unparsed():
    shown = public_hours({"summary": "Book at least two weeks ahead"})
    assert shown["availability_text"] == "Book at least two weeks ahead"
    assert shown["availability"]["days"] is None
    assert shown["is_open_now"] is None
    assert shown["next_change"] is None


def test_by_appointment_without_hours():
    hours = parse_free_text("By appointment, including weekends")
    assert hours["by_appointment"] is True
    assert hours["days"] is None
    assert opening_status(hours) == (None, "By appointment")


def test_overlapping_ranges_rejected():
    with pytest.raises(ValidationError):
        ShopCreate(
            name="Bad",
            slug="bad",
            availability={
                "days": {
                    "mon": {
                        "ranges": [
                            {"open": "08:00", "close": "12:00"},
                            {"open": "11:00", "close": "15:00"},
                        ]
                    }
                }
            },
        )
