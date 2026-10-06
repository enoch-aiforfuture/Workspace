"""Regression: schedule-triggered tasks keep their local time across DST.

The Tasks page converted the picked local time to a fixed UTC "HH:MM" before
saving, and compute_next_run treated it as a UTC wall clock. A task saved as
"daily at 9:00 AM" in America/Chicago during daylight time (14:00 UTC) fired
at 8:00 AM local once DST ended. Weekly tasks could also land on the wrong day
when the UTC conversion crossed midnight, because scheduled_day was not shifted.

Tasks now carry an optional IANA ``timezone``. When set, scheduled_time and
scheduled_day are local to it. That zone wins over a linked crew member.
The crew zone is only the fallback when the task itself has no zone.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes.task.task_routes import _validate_timezone
from src.task_scheduler import _resolve_task_timezone, compute_next_run

CHICAGO = "America/Chicago"


def test_daily_local_time_is_stable_across_dst_end():
    # 2026-11-01: US daylight time ends (CDT UTC-5 -> CST UTC-6).
    before = compute_next_run("daily", "09:00", after=datetime(2026, 10, 31, 12, 0), tz_name=CHICAGO)
    after = compute_next_run("daily", "09:00", after=datetime(2026, 11, 2, 12, 0), tz_name=CHICAGO)
    assert before == datetime(2026, 10, 31, 14, 0)  # 9:00 CDT
    assert after == datetime(2026, 11, 2, 15, 0)    # 9:00 CST


def test_weekly_evening_local_time_keeps_its_weekday():
    # Monday 20:00 in Chicago is Tuesday 01:00 UTC.
    nxt = compute_next_run(
        "weekly", "20:00", scheduled_day=0,
        after=datetime(2026, 10, 3, 12, 0), tz_name=CHICAGO,
    )
    assert nxt == datetime(2026, 10, 6, 1, 0)


def test_task_timezone_used_when_no_crew_member():
    task = SimpleNamespace(crew_member_id=None, timezone=CHICAGO)
    assert _resolve_task_timezone(db=None, task=task) == CHICAGO


def test_task_timezone_wins_over_crew_member():
    class _Query:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return SimpleNamespace(timezone="Europe/London")

    class _DB:
        def query(self, model):
            return _Query()

    task = SimpleNamespace(crew_member_id="crew-1", timezone=CHICAGO)
    assert _resolve_task_timezone(_DB(), task) == CHICAGO


def test_crew_timezone_is_fallback_when_task_has_none():
    class _Query:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return SimpleNamespace(timezone="Europe/London")

    class _DB:
        def query(self, model):
            return _Query()

    task = SimpleNamespace(crew_member_id="crew-1", timezone=None)
    assert _resolve_task_timezone(_DB(), task) == "Europe/London"


def test_task_without_timezone_keeps_legacy_utc():
    task = SimpleNamespace(crew_member_id=None, timezone=None)
    assert _resolve_task_timezone(db=None, task=task) is None


def test_legacy_utc_daily_clock_adopts_browser_zone():
    from src.task_scheduler import adopt_task_schedule_timezone

    task = SimpleNamespace(
        schedule="daily", scheduled_time="14:00", scheduled_day=None,
        timezone=None, cron_expression=None, crew_member_id=None,
    )
    changed = adopt_task_schedule_timezone(
        task, CHICAGO, now=datetime(2026, 10, 6, 12, 0, tzinfo=None),
    )
    assert changed is True
    assert task.timezone == CHICAGO
    assert task.scheduled_time == "09:00"


def test_legacy_weekly_clock_shifts_the_weekday_across_midnight():
    from src.task_scheduler import adopt_task_schedule_timezone

    task = SimpleNamespace(
        schedule="weekly", scheduled_time="02:00", scheduled_day=0,
        timezone=None, cron_expression=None,
    )
    adopt_task_schedule_timezone(
        task, CHICAGO, now=datetime(2026, 10, 6, 12, 0),
    )
    assert task.scheduled_time == "21:00"
    assert task.scheduled_day == 6
    assert task.timezone == CHICAGO


def test_simple_cron_adopts_local_hour_and_dow():
    from src.task_scheduler import adopt_task_schedule_timezone

    task = SimpleNamespace(
        schedule="cron", scheduled_time=None, scheduled_day=None,
        timezone=None, cron_expression="0 2 * * 1",
    )
    adopt_task_schedule_timezone(task, CHICAGO, now=datetime(2026, 10, 6, 12, 0))
    assert task.cron_expression == "0 21 * * 0"
    assert task.timezone == CHICAGO


def test_step_cron_stays_on_the_utc_clock():
    from src.task_scheduler import adopt_task_schedule_timezone

    task = SimpleNamespace(
        schedule="cron", scheduled_time=None, timezone=None,
        cron_expression="0 */2 * * *",
    )
    assert adopt_task_schedule_timezone(task, CHICAGO) is False
    assert task.timezone is None
    assert task.cron_expression == "0 */2 * * *"


def test_crew_zone_is_copied_without_shifting_the_clock():
    from src.task_scheduler import adopt_task_schedule_timezone

    task = SimpleNamespace(
        schedule="daily", scheduled_time="09:00", scheduled_day=None,
        timezone=None, cron_expression=None, crew_member_id="crew-1",
    )
    assert adopt_task_schedule_timezone(
        task, CHICAGO, crew_timezone="Europe/London",
    ) is True
    assert task.scheduled_time == "09:00"
    assert task.timezone == "Europe/London"


def test_server_utc_zone_is_not_an_adoption_zone(monkeypatch):
    from src import task_scheduler

    monkeypatch.setattr(task_scheduler, "_local_zone_key", lambda: "UTC")
    assert task_scheduler.server_iana_timezone() is None

    monkeypatch.setattr(task_scheduler, "_local_zone_key", lambda: "America/Chicago")
    assert task_scheduler.server_iana_timezone() == "America/Chicago"


def test_validate_timezone():
    assert _validate_timezone(CHICAGO) == CHICAGO
    assert _validate_timezone("") is None
    assert _validate_timezone(None) is None
    with pytest.raises(HTTPException) as exc:
        _validate_timezone("Mars/Olympus_Mons")
    assert exc.value.status_code == 400
