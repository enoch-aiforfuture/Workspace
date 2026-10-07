"""Task routes must fail closed when the request has no identity.

Bare get_current_user() plus `if user and task.owner != user` treated a
missing user as single-user mode: list, read, update, delete, and run every
account's tasks, including shell actions. require_user() 401s that caller
when auth is configured and still returns None for AUTH_ENABLED=false.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import ScheduledTask
import routes.task_routes as task_routes


def _request(user, *, api_token=False, host="203.0.113.7"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user, api_token=api_token),
        client=SimpleNamespace(host=host),
        app=SimpleNamespace(state=SimpleNamespace(
            auth_manager=SimpleNamespace(is_configured=True),
        )),
        headers={},
    )


def _endpoint(scheduler, method, path):
    router = task_routes.setup_task_routes(scheduler)
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"{method} {path} not found")


@pytest.fixture
def task_db(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tasks.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(task_routes, "SessionLocal", factory)
    db = factory()
    try:
        for task_id, owner in (("task-alice", "alice"), ("task-bob", "bob")):
            db.add(ScheduledTask(
                id=task_id,
                owner=owner,
                name=task_id,
                prompt="do work",
                task_type="llm",
                trigger_type="webhook",
                status="active",
                output_target="session",
            ))
        db.commit()
    finally:
        db.close()
    return factory


def _scheduler():
    scheduler = MagicMock()
    scheduler.ensure_defaults = AsyncMock()
    scheduler.run_task_now = AsyncMock(return_value=True)
    return scheduler


@pytest.mark.asyncio
async def test_no_identity_cannot_list_or_run_tasks(monkeypatch, task_db):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)
    scheduler = _scheduler()
    anon = _request(None)

    with pytest.raises(HTTPException) as listed:
        await _endpoint(scheduler, "GET", "/api/tasks")(anon)
    assert listed.value.status_code == 401

    with pytest.raises(HTTPException) as fetched:
        await _endpoint(scheduler, "GET", "/api/tasks/{task_id}")(anon, "task-bob")
    assert fetched.value.status_code == 401

    with pytest.raises(HTTPException) as updated:
        await _endpoint(scheduler, "PUT", "/api/tasks/{task_id}")(
            anon, "task-bob", task_routes.TaskUpdate(prompt="pwn"),
        )
    assert updated.value.status_code == 401

    with pytest.raises(HTTPException) as deleted:
        await _endpoint(scheduler, "DELETE", "/api/tasks/{task_id}")(anon, "task-bob")
    assert deleted.value.status_code == 401

    with pytest.raises(HTTPException) as ran:
        await _endpoint(scheduler, "POST", "/api/tasks/{task_id}/run")(anon, "task-bob")
    assert ran.value.status_code == 401
    scheduler.run_task_now.assert_not_called()

    db = task_db()
    try:
        bob = db.query(ScheduledTask).filter(ScheduledTask.id == "task-bob").one()
        assert bob.prompt == "do work"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_api_token_cannot_use_task_routes(monkeypatch, task_db):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    scheduler = _scheduler()
    with pytest.raises(HTTPException) as exc:
        await _endpoint(scheduler, "GET", "/api/tasks")(_request("api", api_token=True))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_authenticated_user_still_cannot_read_another_owners_task(monkeypatch, task_db):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    scheduler = _scheduler()
    with pytest.raises(HTTPException) as exc:
        await _endpoint(scheduler, "GET", "/api/tasks/{task_id}")(_request("alice"), "task-bob")
    assert exc.value.status_code == 403
    own = await _endpoint(scheduler, "GET", "/api/tasks/{task_id}")(_request("alice"), "task-alice")
    assert own["id"] == "task-alice"


@pytest.mark.asyncio
async def test_auth_disabled_keeps_single_user_task_access(monkeypatch, task_db):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    scheduler = _scheduler()
    anon = _request(None)

    listed = await _endpoint(scheduler, "GET", "/api/tasks")(anon)
    assert {task["id"] for task in listed["tasks"]} == {"task-alice", "task-bob"}

    fetched = await _endpoint(scheduler, "GET", "/api/tasks/{task_id}")(anon, "task-bob")
    assert fetched["id"] == "task-bob"
