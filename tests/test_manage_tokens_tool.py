"""manage_tokens must mint tokens the bearer middleware can accept.

The HTTP route stamps wsp_, owner, and normalized scopes, then drops the
auth cache. The agent tool used to skip all four, so a created secret never
authenticated. These tests stay off the real database.
"""
import uuid

import pytest

from src.agent_tools import admin_tools


class _Col:
    """Stand-in for a SQLAlchemy column so ``ApiToken.id == token_id`` works."""

    def __eq__(self, _other):
        return self


class _Token:
    id = _Col()

    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.added = []
        self.deleted = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)
        self.rows.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)

    def commit(self):
        self.commits += 1

    def query(self, _model):
        return _Query(self.rows)

    def close(self):
        pass


@pytest.fixture
def tokens(monkeypatch):
    session = _Session()
    invalidated = []

    import core.database as dbmod

    monkeypatch.setattr(dbmod, "SessionLocal", lambda: session)
    monkeypatch.setattr(dbmod, "ApiToken", _Token)
    monkeypatch.setattr(admin_tools, "_invalidate_api_token_cache", lambda: invalidated.append(True))
    monkeypatch.setattr("secrets.token_urlsafe", lambda _n: "fixedsuffixvalue0123456789abcd")
    monkeypatch.setattr("uuid.uuid4", lambda: uuid.UUID("abcd1234-0000-0000-0000-000000000000"))

    import bcrypt

    monkeypatch.setattr(bcrypt, "hashpw", lambda pw, _salt: b"hash-of:" + pw)
    monkeypatch.setattr(bcrypt, "gensalt", lambda: b"salt")
    return session, invalidated


@pytest.mark.asyncio
async def test_create_stamps_prefix_owner_scopes_and_drops_cache(tokens):
    session, invalidated = tokens

    result = await admin_tools.do_manage_tokens(
        '{"action":"create","name":"  ci bot  ","scopes":"todos:write"}',
        owner="alice",
    )

    assert result["exit_code"] == 0
    assert result["token"].startswith("wsp_")
    assert result["token_prefix"] == result["token"][:8]
    assert result["owner"] == "alice"
    assert result["id"] == "abcd1234"
    # write scopes pull their read pair, matching POST /api/tokens
    assert result["scopes"] == ["todos:read", "todos:write"]
    assert invalidated == [True]
    stored = session.added[0]
    assert stored.owner == "alice"
    assert stored.token_prefix == result["token_prefix"]
    assert stored.scopes == "todos:read,todos:write"
    assert stored.token_hash == "hash-of:" + result["token"]
    assert stored.name == "ci bot"


@pytest.mark.asyncio
async def test_create_profile_overrides_scopes(tokens):
    _session, invalidated = tokens

    result = await admin_tools.do_manage_tokens(
        '{"action":"create","name":"codex","profile":"codex_todos","scopes":"chat"}',
        owner="alice",
    )

    assert result["exit_code"] == 0
    assert result["scopes"] == ["todos:read", "todos:write"]
    assert invalidated == [True]


@pytest.mark.asyncio
async def test_create_without_owner_does_not_mint(tokens):
    session, invalidated = tokens

    result = await admin_tools.do_manage_tokens(
        '{"action":"create","name":"orphan"}',
        owner=None,
    )

    assert result["exit_code"] == 1
    assert "owner" in result["error"]
    assert session.added == []
    assert invalidated == []


@pytest.mark.asyncio
async def test_create_rejects_unknown_scope(tokens):
    session, invalidated = tokens

    result = await admin_tools.do_manage_tokens(
        '{"action":"create","name":"bad","scopes":"shell"}',
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert "Unknown token scope" in result["error"]
    assert session.added == []
    assert invalidated == []


@pytest.mark.asyncio
async def test_delete_refuses_another_owners_token(tokens):
    session, invalidated = tokens
    session.rows.append(_Token(id="tok1", name="bob-token", owner="bob", token_prefix="wsp_abcd", scopes="chat"))

    result = await admin_tools.do_manage_tokens(
        '{"action":"delete","token_id":"tok1"}',
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert result["error"] == "Not your token"
    assert session.deleted == []
    assert session.commits == 0
    assert invalidated == []


@pytest.mark.asyncio
async def test_delete_removes_unowned_legacy_token_and_drops_cache(tokens):
    session, invalidated = tokens
    legacy = _Token(id="old1", name="legacy", owner=None, token_prefix="notwsp_", scopes="")
    session.rows.append(legacy)

    result = await admin_tools.do_manage_tokens(
        '{"action":"delete","token_id":"old1"}',
        owner="alice",
    )

    assert result["exit_code"] == 0
    assert session.deleted == [legacy]
    assert invalidated == [True]


def test_invalidate_api_token_cache_calls_app_hook(monkeypatch):
    from types import SimpleNamespace
    import sys
    import types

    called = []
    mod = types.ModuleType("app")
    mod.app = SimpleNamespace(
        state=SimpleNamespace(invalidate_token_cache=lambda: called.append(True))
    )
    monkeypatch.setitem(sys.modules, "app", mod)

    admin_tools._invalidate_api_token_cache()
    assert called == [True]
