"""Advance repeating note due dates the same way the notes UI does.

``static/js/notes.js`` ``_advanceRecurring`` used to be the only writer that
moved ``due_date`` forward. The background scanner fired a reminder and left
the old date in the past, so email and ntfy reminders stopped after the first
occurrence whenever the browser tab was closed.

Repeat forms match the UI:

- ``daily``, ``yearly``
- ``weekly:W`` (``W`` is 0-6, Sunday-Saturday)
- ``monthly:day:D``, ``monthly:nth:N:W``, ``monthly:last:W``
- legacy ``weekly``, ``monthly``, ``monthly_nth_weekday``, ``monthly_last_weekday``
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone

_MAX_CATCHUP = 5000


def _js_weekday(dt: datetime) -> int:
    """Sunday=0 .. Saturday=6, matching JavaScript ``Date.getDay()``."""
    return (dt.weekday() + 1) % 7


def _parse_local_due(date_str: str) -> datetime | None:
    """Parse a stored due string into a naive local datetime.

    The notes UI writes ``YYYY-MM-DDTHH:MM`` in local time and also accepts a
    trailing ``Z``. Calendar math uses that local wall clock so the reminder
    stays at the same hour after a recurrence step.
    """
    raw = (date_str or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith(("Z", "z")):
            aware = datetime.fromisoformat(raw[:-1]).replace(tzinfo=timezone.utc)
            return aware.astimezone().replace(tzinfo=None)
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is not None:
            return parsed.astimezone().replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def _format_local_due(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def normalize_repeat(repeat: str | None, original: datetime) -> str:
    value = (repeat or "").strip()
    if not value or value == "none":
        return "none"
    if value in ("daily", "yearly"):
        return value
    if value.startswith(("weekly:", "monthly:")):
        return value
    weekday = _js_weekday(original)
    nth = (original.day + 6) // 7  # ceil(day / 7), same as the notes UI
    if value == "weekly":
        return f"weekly:{weekday}"
    if value == "monthly":
        return f"monthly:day:{original.day}"
    if value == "monthly_nth_weekday":
        return f"monthly:nth:{nth}:{weekday}"
    if value == "monthly_last_weekday":
        return f"monthly:last:{weekday}"
    return value


def _add_years(dt: datetime, years: int) -> datetime:
    """Match ``Date.setFullYear``: Feb 29 overflows to Mar 1 in a non-leap year."""
    try:
        return dt.replace(year=dt.year + years)
    except ValueError:
        return dt.replace(year=dt.year + years, month=3, day=1)


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> datetime:
    """``month`` is 1-12. ``weekday`` is Sunday=0. Clamp a missing 5th to the 4th."""
    first_weekday = _js_weekday(datetime(year, month, 1))
    offset = (weekday - first_weekday + 7) % 7
    day = 1 + offset + (n - 1) * 7
    last_day = calendar.monthrange(year, month)[1]
    if day > last_day:
        day -= 7
    return datetime(year, month, day)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> datetime:
    last_day = calendar.monthrange(year, month)[1]
    last = datetime(year, month, last_day)
    back = (_js_weekday(last) - weekday + 7) % 7
    return datetime(year, month, last_day - back)


def _step(current: datetime, norm: str, hour: int, minute: int) -> datetime | None:
    if norm == "daily":
        return current + timedelta(days=1)
    if norm == "yearly":
        return _add_years(current, 1)
    parts = norm.split(":")
    kind = parts[0]
    if kind == "weekly":
        try:
            target = int(parts[1])
        except (IndexError, ValueError):
            return None
        if not 0 <= target <= 6:
            return None
        delta = (target - _js_weekday(current) + 7) % 7
        if delta == 0:
            delta = 7
        nxt = current + timedelta(days=delta)
        return nxt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if kind != "monthly" or len(parts) < 2:
        return None
    year = current.year + (1 if current.month == 12 else 0)
    month = 1 if current.month == 12 else current.month + 1
    sub = parts[1]
    try:
        if sub == "day":
            want = int(parts[2])
            last_day = calendar.monthrange(year, month)[1]
            day = min(max(want, 1), last_day)
            target = datetime(year, month, day)
        elif sub == "nth":
            n = int(parts[2])
            weekday = int(parts[3])
            if n < 1 or not 0 <= weekday <= 6:
                return None
            target = _nth_weekday_of_month(year, month, weekday, n)
        elif sub == "last":
            weekday = int(parts[2])
            if not 0 <= weekday <= 6:
                return None
            target = _last_weekday_of_month(year, month, weekday)
        else:
            return None
    except (IndexError, ValueError):
        return None
    return target.replace(hour=hour, minute=minute, second=0, microsecond=0)


def advance_recurring_due(
    date_str: str,
    repeat: str | None,
    now: datetime | None = None,
) -> str | None:
    """Return the next due string strictly after ``now``, or None.

    Output matches the notes UI: naive local ``YYYY-MM-DDTHH:MM``. Catch-up
    walks forward until that instant is in the future, bounded at 5000 steps.
    """
    original = _parse_local_due(date_str)
    if original is None:
        return None
    norm = normalize_repeat(repeat, original)
    if norm == "none":
        return None
    if now is None:
        cursor = datetime.now()
    elif now.tzinfo is not None:
        cursor = now.astimezone().replace(tzinfo=None)
    else:
        cursor = now
    hour, minute = original.hour, original.minute
    current = original
    guard = _MAX_CATCHUP
    while guard > 0:
        guard -= 1
        nxt = _step(current, norm, hour, minute)
        if nxt is None:
            return None
        current = nxt
        if current > cursor:
            return _format_local_due(current)
    return None
