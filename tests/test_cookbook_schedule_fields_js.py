"""Cookbook schedule form stores the picked hour as local wall clock (#6480 follow-up).

The form used to convert the picked HH:MM to UTC before POST /api/tasks.
Daily and weekly times, and cron hour/minute, are now local when the browser
has an IANA timezone. The Tasks page keeps that zone when a cron task is re-saved.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


def _run(expr, tz="Asia/Tokyo"):
    script = (
        "const fs = require('fs'); const vm = require('vm');"
        "const sandbox = {};"
        "vm.createContext(sandbox);"
        "vm.runInContext(fs.readFileSync('static/js/cookbookScheduleFields.js','utf8'), sandbox);"
        f"console.log(JSON.stringify({expr}));"
    )
    env = dict(os.environ)
    env["TZ"] = tz
    proc = subprocess.run(
        ["node", "-e", script],
        cwd=_REPO, capture_output=True, text=True, timeout=30, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_daily_and_weekly_keep_local_clock_and_zone():
    daily = _run("sandbox.buildCookbookScheduleFields('09:55', ['MO','TU','WE','TH','FR','SA','SU'], 'Asia/Tokyo')")
    assert daily == {"timezone": "Asia/Tokyo", "schedule": "daily", "scheduled_time": "09:55"}

    weekly = _run("sandbox.buildCookbookScheduleFields('21:00', ['MO'], 'America/Chicago')")
    assert weekly["schedule"] == "weekly"
    assert weekly["scheduled_time"] == "21:00"
    assert weekly["scheduled_day"] == 0
    assert weekly["timezone"] == "America/Chicago"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_cron_uses_local_hour_when_zone_is_known():
    weekdays = _run("sandbox.buildCookbookScheduleFields('09:55', ['MO','TU','WE','TH','FR'], 'Asia/Tokyo')")
    assert weekdays["timezone"] == "Asia/Tokyo"
    assert weekdays["cron_expression"] == "55 9 * * 1-5"
    assert "scheduled_time" not in weekdays

    mixed = _run("sandbox.buildCookbookScheduleFields('09:55', ['MO','SU'], 'Asia/Tokyo')")
    assert mixed["cron_expression"] == "55 9 * * 1,0"
    assert mixed["timezone"] == "Asia/Tokyo"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_missing_zone_still_converts_the_picked_hour_to_utc():
    # Asia/Tokyo is UTC+9, so 09:55 local is 00:55 UTC.
    daily = _run("sandbox.buildCookbookScheduleFields('09:55', ['MO','TU','WE','TH','FR','SA','SU'], null)")
    assert daily == {"schedule": "daily", "scheduled_time": "00:55"}
    weekdays = _run("sandbox.buildCookbookScheduleFields('09:55', ['MO','TU','WE','TH','FR'], '')")
    assert weekdays["cron_expression"] == "55 0 * * 1-5"
    assert "timezone" not in weekdays


def test_tasks_page_preserves_timezone_when_resaving_cron():
    text = (_REPO / "static" / "js" / "tasks.js").read_text(encoding="utf-8")
    assert "if (!existing?.timezone) payload.timezone = '';" in text
    schedule = (_REPO / "static" / "js" / "cookbookSchedule.js").read_text(encoding="utf-8")
    assert "_localHHMMToUtc" not in schedule
    assert "buildCookbookScheduleFields(startTime, days, _browserTimeZone())" in schedule
