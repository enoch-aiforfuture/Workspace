"""Imported recurring events expand in their source timezone (#6512).

A TZID event is stored as a naive UTC instant. Expanding that instant makes
BYDAY match the UTC weekday, so a Monday-21:00 America/Los_Angeles series
lands on Sunday and shifts an hour when daylight time ends. Expansion uses
the stored zone and converts each occurrence back to UTC.
"""

from datetime import datetime, timezone

import pytest

from src.caldav_sync import _source_tzid
from src.caldav_writeback import build_event_ical
from tests.helpers.calendar_routes import import_calendar_routes
from tests.test_calendar_recurrence import _make_event

LA = "America/Los_Angeles"


def _la_series():
    # Mon 2026-06-01 21:00 PDT == Tue 2026-06-02 04:00 UTC.
    return _make_event(
        dtstart=datetime(2026, 6, 2, 4, 0),
        dtend=datetime(2026, 6, 2, 5, 0),
        is_utc=True,
        timezone=LA,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )


def test_byday_stays_on_the_source_weekday_across_dst():
    cal = import_calendar_routes()
    results = cal._expand_rrule(_la_series(), datetime(2026, 6, 1), datetime(2026, 12, 1))
    assert results, "the first Monday must not be dropped"
    assert results[0]["dtstart"] == "2026-06-02T04:00:00Z"

    zone = pytest.importorskip("zoneinfo").ZoneInfo(LA)
    local_days = []
    local_hours = []
    for row in results:
        raw = row["dtstart"].replace("Z", "+00:00")
        local = datetime.fromisoformat(raw).astimezone(zone)
        local_days.append(local.strftime("%a"))
        local_hours.append(local.hour)
    assert local_days[0] == "Mon"
    assert set(local_days) == {"Mon"}
    assert set(local_hours) == {21}
    # After US DST ends (2026-11-01) the UTC instant moves from 04:00 to 05:00.
    assert "2026-11-03T05:00:00Z" in [row["dtstart"] for row in results]


def test_exdate_matches_the_utc_occurrence_key():
    cal = import_calendar_routes()
    ev = _la_series()
    ev.recurrence_exdates = '["2026-06-02T04:00"]'
    results = cal._expand_rrule(ev, datetime(2026, 6, 1), datetime(2026, 7, 1))
    starts = [row["dtstart"] for row in results]
    assert "2026-06-02T04:00:00Z" not in starts
    assert starts[0] == "2026-06-09T04:00:00Z"


def test_row_without_timezone_keeps_the_stored_clock():
    cal = import_calendar_routes()
    ev = _make_event(
        dtstart=datetime(2026, 6, 2, 4, 0),
        dtend=datetime(2026, 6, 2, 5, 0),
        is_utc=True,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )
    results = cal._expand_rrule(ev, datetime(2026, 6, 1), datetime(2026, 6, 16))
    assert results[0]["dtstart"] == "2026-06-08T04:00:00Z"


def test_unknown_timezone_falls_back_to_the_stored_clock():
    cal = import_calendar_routes()
    ev = _la_series()
    ev.timezone = "Mars/Olympus_Mons"
    results = cal._expand_rrule(ev, datetime(2026, 6, 1), datetime(2026, 6, 16))
    assert results[0]["dtstart"] == "2026-06-08T04:00:00Z"


def test_source_tzid_reads_the_ical_parameter():
    icalendar = pytest.importorskip("icalendar")
    zone = pytest.importorskip("zoneinfo").ZoneInfo(LA)
    component = icalendar.Event()
    component.add("dtstart", datetime(2026, 6, 1, 21, 0, tzinfo=zone))
    assert _source_tzid(component.get("dtstart")) == LA
    assert _source_tzid(None) is None


def test_writeback_emits_the_source_tzid():
    ical = build_event_ical({
        "uid": "evt-1",
        "summary": "Evening",
        "dtstart": datetime(2026, 6, 2, 4, 0),
        "dtend": datetime(2026, 6, 2, 5, 0),
        "all_day": False,
        "is_utc": True,
        "timezone": LA,
        "rrule": "FREQ=WEEKLY;BYDAY=MO",
    })
    assert "TZID=America/Los_Angeles" in ical
    assert "T210000" in ical
    assert "DTSTART:20260602T040000Z" not in ical


def test_writeback_without_timezone_stays_utc():
    ical = build_event_ical({
        "uid": "evt-1",
        "summary": "UTC",
        "dtstart": datetime(2026, 6, 10, 14, 0),
        "dtend": datetime(2026, 6, 10, 15, 0),
        "all_day": False,
        "is_utc": True,
        "rrule": "",
    })
    assert "DTSTART:20260610T140000Z" in ical
