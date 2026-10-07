"""Reminder webhooks and ntfy send a bearer token. The connect must use the
address snapshot from the safety check and must not follow a redirect.
"""
import asyncio

import routes.note_routes as notes
from src.pinned_fetch import _PinnedAsyncTransport


class _Redirect:
    status_code = 302
    is_success = False
    headers = {"location": "http://169.254.169.254/latest/meta-data"}


class _Client:
    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        _Client.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, headers=None, json=None, content=None):
        self.calls.append((method, url, headers, content))
        return _Redirect()


def _dispatch(**settings):
    base = {
        "reminder_llm_synthesis": False,
        "reminder_ntfy_topic": "reminders",
    }
    base.update(settings)
    return asyncio.run(notes.dispatch_reminder(
        "Title", "Body", note_id="", queue_browser=True, settings_override=base,
    ))


def test_ntfy_post_is_pinned_and_does_not_follow_redirect(monkeypatch):
    _Client.instances = []
    monkeypatch.delenv("REMINDER_WEBHOOK_BLOCK_PRIVATE_IPS", raising=False)
    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(
        "src.integrations.load_integrations",
        lambda: [{
            "preset": "ntfy",
            "enabled": True,
            "base_url": "https://ntfy.example",
            "api_key": "secret-token",
            "name": "ntfy",
        }],
    )
    monkeypatch.setattr("src.pinned_fetch.httpx.AsyncClient", _Client)

    result = _dispatch(reminder_channel="ntfy")

    assert result["ntfy_sent"] is False
    assert "302" in result["ntfy_error"]
    assert len(_Client.instances) == 1
    pinned = _Client.instances[0]
    assert pinned.kwargs["follow_redirects"] is False
    transport = pinned.kwargs["transport"]
    assert isinstance(transport, _PinnedAsyncTransport)
    assert [str(ip) for ip in transport._pinned_ips] == ["93.184.216.34"]
    assert len(pinned.calls) == 1
    method, url, headers, content = pinned.calls[0]
    assert method == "POST"
    assert url == "https://ntfy.example/reminders"
    assert "169.254.169.254" not in url
    assert headers["Authorization"] == "Bearer secret-token"
    assert content == "Body"


def test_webhook_post_is_pinned_to_the_checked_address(monkeypatch):
    _Client.instances = []
    monkeypatch.delenv("REMINDER_WEBHOOK_BLOCK_PRIVATE_IPS", raising=False)
    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(
        "src.integrations.load_integrations",
        lambda: [{
            "id": "wh1",
            "preset": "custom",
            "enabled": True,
            "base_url": "https://hooks.example/reminder",
            "api_key": "wh-secret",
            "auth_type": "bearer",
        }],
    )
    monkeypatch.setattr("src.pinned_fetch.httpx.AsyncClient", _Client)

    result = _dispatch(
        reminder_channel="webhook",
        reminder_webhook_integration_id="wh1",
        reminder_webhook_payload_template='{"text":"{{message}}"}',
    )

    assert result["webhook_sent"] is False
    assert len(_Client.instances) == 1
    method, url, headers, content = _Client.instances[0].calls[0]
    assert method == "POST"
    assert url == "https://hooks.example/reminder"
    assert headers["Authorization"] == "Bearer wh-secret"
    assert b"Body" in content
    assert [str(ip) for ip in _Client.instances[0].kwargs["transport"]._pinned_ips] == ["93.184.216.34"]


def test_ntfy_link_local_is_rejected_before_a_client(monkeypatch):
    _Client.instances = []
    monkeypatch.setattr(
        "src.integrations.load_integrations",
        lambda: [{
            "preset": "ntfy",
            "enabled": True,
            "base_url": "http://169.254.169.254",
            "api_key": "secret-token",
            "name": "ntfy",
        }],
    )
    monkeypatch.setattr("src.pinned_fetch.httpx.AsyncClient", _Client)

    result = _dispatch(reminder_channel="ntfy")

    assert result["ntfy_sent"] is False
    assert "rejected" in result["ntfy_error"].lower()
    assert _Client.instances == []
