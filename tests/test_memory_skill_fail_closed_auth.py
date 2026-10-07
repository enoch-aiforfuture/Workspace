"""Memory and skill routes must fail closed when the request has no identity.

Both stores return every row when owner is None, and their per-item checks
returned immediately for a missing user. That missing user was treated as
auth-disabled single-user mode even when auth was configured.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import routes.memory_routes as memory_routes
import routes.skills_routes as skills_routes


def _request(user, *, api_token=False, host="203.0.113.7"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user, api_token=api_token),
        client=SimpleNamespace(host=host),
        app=SimpleNamespace(state=SimpleNamespace(
            auth_manager=SimpleNamespace(is_configured=True),
        )),
    )


def _endpoint(router, method, path):
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"{method} {path} not found")


class _MemoryStore:
    def __init__(self):
        self.loads = []
        self.saved = None

    def load(self, owner=None):
        self.loads.append(owner)
        rows = [
            {"id": "mem-alice", "text": "alice secret", "owner": "alice"},
            {"id": "mem-bob", "text": "bob secret", "owner": "bob"},
        ]
        if owner is None:
            return rows
        return [row for row in rows if row["owner"] == owner]

    def load_all_for_update(self):
        return self.load(owner=None)

    def save(self, rows):
        self.saved = rows


class _SkillStore:
    def __init__(self):
        self.loads = []

    def load(self, owner=None):
        self.loads.append(owner)
        rows = [
            {"id": "skill-alice", "name": "alice-skill", "owner": "alice"},
            {"id": "skill-bob", "name": "bob-skill", "owner": "bob"},
        ]
        if owner is None:
            return rows
        return [row for row in rows if row["owner"] == owner]

    def index_for(self, owner=None):
        return [{"name": row["name"]} for row in self.load(owner=owner)]


@pytest.mark.asyncio
async def test_no_identity_cannot_list_memories_or_skills(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)
    memories = _MemoryStore()
    skills = _SkillStore()
    anon = _request(None)

    memory_list = _endpoint(memory_routes.setup_memory_routes(memories, MagicMock()), "GET", "/api/memory")
    with pytest.raises(HTTPException) as mem_exc:
        memory_list(anon)
    assert mem_exc.value.status_code == 401
    assert memories.loads == []

    skill_list = _endpoint(skills_routes.setup_skills_routes(skills), "GET", "/api/skills")
    with pytest.raises(HTTPException) as skill_exc:
        await skill_list(anon)
    assert skill_exc.value.status_code == 401
    assert skills.loads == []

    pin = _endpoint(
        memory_routes.setup_memory_routes(memories, MagicMock()),
        "POST",
        "/api/memory/{memory_id}/pin",
    )
    with pytest.raises(HTTPException) as pin_exc:
        pin(anon, "mem-bob", True)
    assert pin_exc.value.status_code == 401
    assert memories.saved is None


@pytest.mark.asyncio
async def test_api_token_cannot_list_memories_or_skills(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    token = _request("api", api_token=True)
    with pytest.raises(HTTPException) as mem_exc:
        _endpoint(memory_routes.setup_memory_routes(_MemoryStore(), MagicMock()), "GET", "/api/memory")(token)
    assert mem_exc.value.status_code == 403
    with pytest.raises(HTTPException) as skill_exc:
        await _endpoint(skills_routes.setup_skills_routes(_SkillStore()), "GET", "/api/skills")(token)
    assert skill_exc.value.status_code == 403


@pytest.mark.asyncio
async def test_auth_disabled_keeps_single_user_memory_and_skill_access(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    memories = _MemoryStore()
    skills = _SkillStore()
    anon = _request(None)

    listed = _endpoint(memory_routes.setup_memory_routes(memories, MagicMock()), "GET", "/api/memory")(anon)
    assert {row["id"] for row in listed["memory"]} == {"mem-alice", "mem-bob"}

    skill_rows = await _endpoint(skills_routes.setup_skills_routes(skills), "GET", "/api/skills")(anon)
    assert {row["id"] for row in skill_rows["skills"]} == {"skill-alice", "skill-bob"}
