"""manage_webhooks must keep signing secrets the way POST /api/webhooks does.

Delivery signs X-Workspace-Signature only when the stored secret decrypts.
The agent tool used to drop `secret` on add and never report whether a
webhook was signed.
"""
import uuid

import pytest

from src.agent_tools import admin_tools


class _Col:
    def __eq__(self, _other):
        return self


class _Hook:
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

    def add(self, obj):
        self.added.append(obj)
        self.rows.append(obj)

    def delete(self, obj):
        self.rows.remove(obj)

    def commit(self):
        pass

    def query(self, _model):
        return _Query(self.rows)

    def close(self):
        pass


@pytest.fixture
def hooks(monkeypatch):
    session = _Session()
    import core.database as dbmod

    monkeypatch.setattr(dbmod, "SessionLocal", lambda: session)
    monkeypatch.setattr(dbmod, "Webhook", _Hook)
    monkeypatch.setattr(
        "src.webhook_manager.validate_webhook_url",
        lambda url: url.strip(),
    )
    monkeypatch.setattr("uuid.uuid4", lambda: uuid.UUID("abcd1234-0000-0000-0000-000000000000"))
    return session


@pytest.mark.asyncio
async def test_add_encrypts_secret_and_does_not_echo_it(hooks, monkeypatch):
    import sys
    import types

    class _Keys:
        def encrypt_api_key(self, value):
            return "enc:" + value

    app_mod = types.ModuleType("app")
    app_mod.api_key_manager = _Keys()
    monkeypatch.setitem(sys.modules, "app", app_mod)

    secret = "signing-secret-value"
    result = await admin_tools.do_manage_webhooks(
        '{"action":"add","name":"ci","url":"https://hooks.example/workspace",'
        '"secret":"%s","events":["chat.completed","chat.message"]}' % secret,
        owner="alice",
    )

    assert result["exit_code"] == 0
    assert result["has_secret"] is True
    assert result["id"] == "abcd1234"
    assert secret not in str(result)
    stored = hooks.added[0]
    assert stored.secret == "enc:" + secret
    assert stored.events == "chat.completed,chat.message"
    assert stored.name == "ci"


@pytest.mark.asyncio
async def test_add_without_secret_stores_none(hooks, monkeypatch):
    import sys
    import types

    app_mod = types.ModuleType("app")
    app_mod.api_key_manager = None
    monkeypatch.setitem(sys.modules, "app", app_mod)

    result = await admin_tools.do_manage_webhooks(
        '{"action":"add","url":"https://hooks.example/workspace"}',
        owner="alice",
    )

    assert result["exit_code"] == 0
    assert result["has_secret"] is False
    assert hooks.added[0].secret is None
    assert hooks.added[0].events == "chat.completed"
    assert hooks.added[0].name == "https://hooks.example/workspace"


@pytest.mark.asyncio
async def test_list_reports_has_secret_without_the_value(hooks):
    hooks.rows.append(_Hook(
        id="wh1", name="ci", url="https://hooks.example/workspace",
        secret="enc:hidden", events="chat.completed", is_active=True,
    ))

    result = await admin_tools.do_manage_webhooks('{"action":"list"}', owner="alice")

    assert result["exit_code"] == 0
    row = result["webhooks"][0]
    assert row["has_secret"] is True
    assert "secret" not in row
    assert "enc:hidden" not in str(result)


@pytest.mark.asyncio
async def test_add_rejects_unknown_event(hooks):
    result = await admin_tools.do_manage_webhooks(
        '{"action":"add","url":"https://hooks.example/workspace","events":"nope"}',
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert "Invalid events" in result["error"]
    assert hooks.added == []
