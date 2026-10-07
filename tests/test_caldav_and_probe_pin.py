"""CalDAV test connects and model/health probes must use the checked address.

The settings test sends the CalDAV password. Health probes can carry
credentials in the URL. Model discovery and context probes talk to a
configured host. A later DNS answer must not move those requests onto a
link-local address.
"""
import ssl
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.calendar_routes import setup_calendar_routes
from src.pinned_fetch import PinnedFetchError


def test_health_probe_rejects_a_link_local_answer(monkeypatch):
    from src.service_health import _http_get

    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["169.254.169.254"])

    with pytest.raises(PinnedFetchError, match="link-local"):
        _http_get("https://ntfy.example/v1/health", timeout=1)


def test_model_discovery_probe_rejects_a_link_local_answer(monkeypatch):
    from src.model_discovery import ModelDiscovery

    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["169.254.169.254"])

    assert ModelDiscovery(default_host="models.example")._check_port("models.example", 1234) is None


def test_caldav_test_connection_rejects_a_link_local_answer(monkeypatch):
    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["169.254.169.254"])
    monkeypatch.setattr("src.caldav_sync.validate_caldav_url", lambda url: url.rstrip("/"))
    monkeypatch.setattr("routes.calendar_routes._require_user", lambda request: "owner")

    with patch("routes.calendar_routes._require_user", return_value="owner"):
        router = setup_calendar_routes()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.post(
        "/api/calendar/test",
        json={"url": "https://cal.example.com/dav", "username": "u", "password": "secret"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "link-local" in body["error"]


def test_caldav_test_connection_pins_the_client_backend(monkeypatch):
    """A public answer is pinned onto the client before the password is sent."""
    seen = {}

    class _Pool:
        def __init__(self):
            self._network_backend = object()

    class _Transport:
        def __init__(self):
            self._pool = _Pool()

    class _Client:
        def __init__(self, **kwargs):
            seen["kwargs"] = kwargs
            self._transport = _Transport()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, **kwargs):
            seen["backend"] = self._transport._pool._network_backend
            seen["request"] = (method, url, kwargs.get("auth"))
            return httpx.Response(207, request=httpx.Request(method, url))

    monkeypatch.delenv("WORKSPACE_ALLOW_PRIVATE_CALDAV", raising=False)
    monkeypatch.setattr("src.caldav_sync.validate_caldav_url", lambda url: url.rstrip("/"))
    monkeypatch.setattr("routes.calendar_routes._require_user", lambda request: "owner")
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    router = setup_calendar_routes()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.post(
        "/api/calendar/test",
        json={"url": "https://cal.example.com/dav", "username": "u", "password": "secret"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert seen["kwargs"]["follow_redirects"] is False
    assert seen["kwargs"]["trust_env"] is False
    assert isinstance(seen["kwargs"]["verify"], ssl.SSLContext)
    assert seen["request"] == ("PROPFIND", "https://cal.example.com/dav", ("u", "secret"))
    from src.pinned_fetch import _CheckingAsyncBackend
    assert isinstance(seen["backend"], _CheckingAsyncBackend)
    assert seen["backend"]._block_private is True
