"""Recurring note due dates advance on the server, not only in the browser."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.builtin_actions import TaskNoop, action_ping_notes
from src.note_repeat import advance_recurring_due


def test_daily_keeps_the_clock_and_skips_missed_days():
    nxt = advance_recurring_due(
        "2026-10-01T09:30", "daily", now=datetime(2026, 10, 6, 8, 0)
    )
    assert nxt == "2026-10-06T09:30"


def test_daily_rolls_to_tomorrow_once_todays_time_has_passed():
    nxt = advance_recurring_due(
        "2026-10-01T09:30", "daily", now=datetime(2026, 10, 6, 10, 0)
    )
    assert nxt == "2026-10-07T09:30"


def test_daily_on_the_exact_instant_moves_forward():
    nxt = advance_recurring_due(
        "2026-10-06T09:00", "daily", now=datetime(2026, 10, 6, 9, 0)
    )
    assert nxt == "2026-10-07T09:00"


def test_weekly_uses_sunday_zero_weekday():
    # 2026-09-30 is a Wednesday (JS weekday 3). Next Wednesday after
    # Tuesday 2026-10-06 is 2026-10-07.
    nxt = advance_recurring_due(
        "2026-09-30T15:00", "weekly:3", now=datetime(2026, 10, 6, 10, 0)
    )
    assert nxt == "2026-10-07T15:00"


def test_legacy_weekly_derives_the_weekday_from_the_original_date():
    nxt = advance_recurring_due(
        "2026-10-07T09:00", "weekly", now=datetime(2026, 10, 7, 9, 1)
    )
    assert nxt == "2026-10-14T09:00"


def test_monthly_day_clamps_to_the_shorter_month_then_restores():
    february = advance_recurring_due(
        "2026-01-31T08:00", "monthly:day:31", now=datetime(2026, 2, 1, 0, 0)
    )
    assert february == "2026-02-28T08:00"
    march = advance_recurring_due(
        "2026-02-28T08:00", "monthly:day:31", now=datetime(2026, 3, 1, 0, 0)
    )
    assert march == "2026-03-31T08:00"


def test_monthly_nth_and_last_weekday():
    second_tuesday = advance_recurring_due(
        "2026-09-08T07:15", "monthly:nth:2:2", now=datetime(2026, 10, 1, 0, 0)
    )
    assert second_tuesday == "2026-10-13T07:15"
    last_friday = advance_recurring_due(
        "2026-09-25T18:00", "monthly:last:5", now=datetime(2026, 10, 1, 0, 0)
    )
    assert last_friday == "2026-10-30T18:00"


def test_yearly_feb_29_overflows_like_the_notes_ui():
    nxt = advance_recurring_due(
        "2024-02-29T12:00", "yearly", now=datetime(2024, 3, 1, 0, 0)
    )
    assert nxt == "2025-03-01T12:00"


def test_none_and_malformed_repeat_do_not_advance():
    now = datetime(2026, 10, 6, 12, 0)
    assert advance_recurring_due("2026-10-01T09:00", "none", now=now) is None
    assert advance_recurring_due("2026-10-01T09:00", "", now=now) is None
    assert advance_recurring_due("2026-10-01T09:00", "weekly:9", now=now) is None
    assert advance_recurring_due("not-a-date", "daily", now=now) is None


def test_zulu_due_advances_in_local_time():
    start = datetime(2026, 10, 6, 13, 0, tzinfo=timezone.utc)
    local = start.astimezone().replace(tzinfo=None)
    nxt = advance_recurring_due(
        "2026-10-06T13:00:00Z",
        "daily",
        now=local + timedelta(minutes=1),
    )
    expected = (local + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    assert nxt == expected


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.committed = 0

    def query(self, model):
        return _Rows(self.rows)

    def commit(self):
        self.committed += 1

    def close(self):
        pass


def _patch_scanner(monkeypatch, tmp_path, notes, dispatch):
    from src import builtin_actions

    session = _Session(notes)
    monkeypatch.setattr(builtin_actions, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr("core.database.SessionLocal", lambda: session)
    monkeypatch.setattr("routes.note_routes.dispatch_reminder", dispatch)
    return session


@pytest.mark.asyncio
async def test_ping_notes_advances_daily_after_a_successful_send(monkeypatch, tmp_path):
    due = datetime.now().replace(second=0, microsecond=0)
    note = SimpleNamespace(
        id="n1", owner="", title="Standup", content="bring notes",
        items=None, due_date=due.strftime("%Y-%m-%dT%H:%M"),
        repeat="daily", archived=False,
    )
    calls = []

    async def dispatch(**kwargs):
        calls.append(kwargs)
        return {"email_sent": True}

    session = _patch_scanner(monkeypatch, tmp_path, [note], dispatch)
    message, ok = await action_ping_notes(owner="")
    assert ok is True
    assert "Standup" in message
    assert len(calls) == 1
    assert note.due_date == (due + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    assert session.committed == 1


@pytest.mark.asyncio
async def test_ping_notes_rolls_a_missed_daily_forward_without_sending(monkeypatch, tmp_path):
    # Twelve hours ago, so the next daily slot is twelve hours ahead and
    # cannot fall inside the scanner's ±90s send window.
    stale = datetime.now().replace(second=0, microsecond=0) - timedelta(hours=12)
    note = SimpleNamespace(
        id="n2", owner="", title="Meds", content="",
        items=None, due_date=stale.strftime("%Y-%m-%dT%H:%M"),
        repeat="daily", archived=False,
    )
    calls = []

    async def dispatch(**kwargs):
        calls.append(kwargs)

    session = _patch_scanner(monkeypatch, tmp_path, [note], dispatch)
    with pytest.raises(TaskNoop):
        await action_ping_notes(owner="")
    assert calls == []
    assert note.due_date == (stale + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    assert session.committed == 1


@pytest.mark.asyncio
async def test_ping_notes_leaves_a_one_shot_due_date_alone(monkeypatch, tmp_path):
    due = datetime.now().replace(second=0, microsecond=0)
    original = due.strftime("%Y-%m-%dT%H:%M")
    note = SimpleNamespace(
        id="n3", owner="", title="Once", content="",
        items=None, due_date=original, repeat="none", archived=False,
    )

    async def dispatch(**kwargs):
        return {"email_sent": True}

    session = _patch_scanner(monkeypatch, tmp_path, [note], dispatch)
    message, ok = await action_ping_notes(owner="")
    assert ok is True
    assert note.due_date == original
    assert "Once" in message
    assert session.committed == 0


@pytest.mark.asyncio
async def test_failed_dispatch_does_not_skip_the_occurrence(monkeypatch, tmp_path):
    due = datetime.now().replace(second=0, microsecond=0)
    original = due.strftime("%Y-%m-%dT%H:%M")
    note = SimpleNamespace(
        id="n4", owner="", title="Retry", content="",
        items=None, due_date=original, repeat="daily", archived=False,
    )

    async def dispatch(**kwargs):
        raise RuntimeError("smtp down")

    session = _patch_scanner(monkeypatch, tmp_path, [note], dispatch)
    with pytest.raises(TaskNoop):
        await action_ping_notes(owner="")
    assert note.due_date == original
    assert session.committed == 0
