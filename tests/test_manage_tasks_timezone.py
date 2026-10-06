"""manage_tasks stores scheduled_time as a local wall clock plus an IANA zone.

The Tasks page does this. The agent tool used to document the clock as UTC
and clear a stored timezone whenever it changed the time, so the next DST
transition shifted the hour.
"""
import json
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
from core.database import ScheduledTask
from src.task_scheduler import compute_next_run
from src.tools.system import do_manage_tasks
from src.user_time import clear_user_time_context, set_user_tz_name, set_user_tz_offset

CHICAGO = "America/Chicago"


@pytest.fixture
def task_db(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    engine = create_engine(
        f"sqlite:///{tmp.name}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(cdb, "SessionLocal", Session)
    clear_user_time_context()
    yield Session
    clear_user_time_context()


def _fields(Session, task_id):
    db = Session()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).one()
        return {
            "scheduled_time": task.scheduled_time,
            "timezone": task.timezone,
            "next_run": task.next_run,
            "schedule": task.schedule,
            "name": task.name,
        }
    finally:
        db.close()


def _seed(Session, task_id, **kwargs):
    db = Session()
    try:
        db.add(ScheduledTask(
            id=task_id,
            owner=kwargs.get("owner", "alice"),
            name=kwargs.get("name", task_id),
            prompt="original",
            task_type="llm",
            trigger_type="schedule",
            schedule=kwargs.get("schedule", "daily"),
            scheduled_time=kwargs.get("scheduled_time", "09:00"),
            timezone=kwargs.get("timezone"),
            status="active",
            output_target="session",
        ))
        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_create_stores_local_clock_with_browser_zone(task_db):
    set_user_tz_name(CHICAGO)
    out = await do_manage_tasks(
        json.dumps({
            "action": "create",
            "name": "Morning",
            "prompt": "Summarize the inbox",
            "schedule": "daily",
            "scheduled_time": "09:00",
        }),
        owner="alice",
    )
    assert out["exit_code"] == 0
    fields = _fields(task_db, out["task_id"])
    assert fields["scheduled_time"] == "09:00"
    assert fields["timezone"] == CHICAGO
    assert fields["next_run"] == compute_next_run("daily", "09:00", tz_name=CHICAGO)


@pytest.mark.asyncio
async def test_create_without_iana_name_keeps_utc_clock(task_db):
    set_user_tz_offset(600)
    out = await do_manage_tasks(
        json.dumps({
            "action": "create",
            "prompt": "Summarize the inbox",
            "scheduled_time": "09:00",
        }),
        owner="alice",
    )
    assert out["exit_code"] == 0
    fields = _fields(task_db, out["task_id"])
    assert fields["scheduled_time"] == "09:00"
    assert fields["timezone"] is None
    assert fields["next_run"] == compute_next_run("daily", "09:00", tz_name=None)


@pytest.mark.asyncio
async def test_explicit_timezone_overrides_browser_zone(task_db):
    set_user_tz_name(CHICAGO)
    out = await do_manage_tasks(
        json.dumps({
            "action": "create",
            "prompt": "Summarize the inbox",
            "scheduled_time": "08:30",
            "timezone": "Europe/London",
        }),
        owner="alice",
    )
    assert out["exit_code"] == 0
    fields = _fields(task_db, out["task_id"])
    assert fields["timezone"] == "Europe/London"
    assert fields["scheduled_time"] == "08:30"


@pytest.mark.asyncio
async def test_unknown_timezone_is_rejected(task_db):
    out = await do_manage_tasks(
        json.dumps({
            "action": "create",
            "prompt": "Summarize the inbox",
            "scheduled_time": "09:00",
            "timezone": "Mars/Olympus_Mons",
        }),
        owner="alice",
    )
    assert out["exit_code"] == 1
    assert "Unknown timezone" in out["error"]


@pytest.mark.asyncio
async def test_edit_keeps_stored_timezone_when_time_changes(task_db):
    _seed(task_db, "keep-zone", timezone=CHICAGO, scheduled_time="09:00")
    clear_user_time_context()
    out = await do_manage_tasks(
        json.dumps({"action": "edit", "task_id": "keep-zone", "scheduled_time": "08:00"}),
        owner="alice",
    )
    assert out["exit_code"] == 0
    fields = _fields(task_db, "keep-zone")
    assert fields["scheduled_time"] == "08:00"
    assert fields["timezone"] == CHICAGO
    assert fields["next_run"] == compute_next_run("daily", "08:00", tz_name=CHICAGO)


@pytest.mark.asyncio
async def test_edit_of_legacy_utc_task_adopts_browser_zone(task_db):
    _seed(task_db, "legacy", timezone=None, scheduled_time="14:00")
    set_user_tz_name(CHICAGO)
    out = await do_manage_tasks(
        json.dumps({"action": "edit", "task_id": "legacy", "scheduled_time": "09:00"}),
        owner="alice",
    )
    assert out["exit_code"] == 0
    fields = _fields(task_db, "legacy")
    assert fields["scheduled_time"] == "09:00"
    assert fields["timezone"] == CHICAGO


@pytest.mark.asyncio
async def test_edit_without_iana_name_does_not_invent_a_zone(task_db):
    _seed(task_db, "offset-only", timezone=None, scheduled_time="14:00")
    set_user_tz_offset(-300)
    out = await do_manage_tasks(
        json.dumps({"action": "edit", "task_id": "offset-only", "scheduled_time": "09:00"}),
        owner="alice",
    )
    assert out["exit_code"] == 0
    fields = _fields(task_db, "offset-only")
    assert fields["scheduled_time"] == "09:00"
    assert fields["timezone"] is None


@pytest.mark.asyncio
async def test_edit_can_clear_timezone_explicitly(task_db):
    _seed(task_db, "clear-zone", timezone=CHICAGO, scheduled_time="09:00")
    out = await do_manage_tasks(
        json.dumps({
            "action": "edit",
            "task_id": "clear-zone",
            "scheduled_time": "10:00",
            "timezone": "",
        }),
        owner="alice",
    )
    assert out["exit_code"] == 0
    fields = _fields(task_db, "clear-zone")
    assert fields["scheduled_time"] == "10:00"
    assert fields["timezone"] is None
    assert fields["next_run"] == compute_next_run("daily", "10:00", tz_name=None)


@pytest.mark.asyncio
async def test_invalid_edit_timezone_does_not_change_the_time(task_db):
    _seed(task_db, "bad-edit", timezone=CHICAGO, scheduled_time="09:00")
    out = await do_manage_tasks(
        json.dumps({
            "action": "edit",
            "task_id": "bad-edit",
            "scheduled_time": "11:00",
            "timezone": "Not/AZone",
        }),
        owner="alice",
    )
    assert out["exit_code"] == 1
    fields = _fields(task_db, "bad-edit")
    assert fields["scheduled_time"] == "09:00"
    assert fields["timezone"] == CHICAGO


@pytest.mark.asyncio
async def test_list_labels_local_and_utc_clocks(task_db):
    _seed(task_db, "local-task", name="Local", timezone=CHICAGO, scheduled_time="09:00")
    _seed(task_db, "utc-task", name="Utc", timezone=None, scheduled_time="15:00")
    out = await do_manage_tasks(json.dumps({"action": "list"}), owner="alice")
    assert out["exit_code"] == 0
    assert "09:00 America/Chicago" in out["response"]
    assert "15:00 UTC" in out["response"]
