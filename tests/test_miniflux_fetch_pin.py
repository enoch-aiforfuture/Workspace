"""Check-in sends the Miniflux token. The connect must use the address
snapshot from the safety check and must not follow a redirect.
"""
import asyncio

import pytest

from src.pinned_fetch import PinnedFetchError, _PinnedAsyncTransport
from src.task_scheduler import fetch_miniflux_unread


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


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
        self.calls.append((method, url, headers))
        if "redirect" in url:
            return _Resp(302)
        return _Resp(200, {
            "entries": [{
                "title": "Hello",
                "url": "https://news.example/hello",
                "feed": {"title": "News"},
            }],
        })


def test_miniflux_fetch_is_pinned_and_ignores_redirect(monkeypatch):
    _Client.instances = []
    monkeypatch.delenv("INTEGRATION_API_BLOCK_PRIVATE_IPS", raising=False)
    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["93.184.216.34"])
    monkeypatch.setattr("src.pinned_fetch.httpx.AsyncClient", _Client)

    text = asyncio.run(fetch_miniflux_unread(
        "https://rss.example",
        {"X-Auth-Token": "secret-token"},
    ))

    assert text == "- [News] Hello — https://news.example/hello"
    assert len(_Client.instances) == 1
    pinned = _Client.instances[0]
    assert pinned.kwargs["follow_redirects"] is False
    transport = pinned.kwargs["transport"]
    assert isinstance(transport, _PinnedAsyncTransport)
    assert [str(ip) for ip in transport._pinned_ips] == ["93.184.216.34"]
    assert len(pinned.calls) == 1
    method, url, headers = pinned.calls[0]
    assert method == "GET"
    assert url.startswith("https://rss.example/v1/entries?")
    assert "status=unread" in url
    assert "limit=15" in url
    assert "169.254.169.254" not in url
    assert headers["X-Auth-Token"] == "secret-token"


def test_miniflux_redirect_is_not_followed(monkeypatch):
    _Client.instances = []
    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["93.184.216.34"])
    monkeypatch.setattr("src.pinned_fetch.httpx.AsyncClient", _Client)

    text = asyncio.run(fetch_miniflux_unread(
        "https://redirect.example",
        {"Authorization": "Bearer secret-token"},
    ))

    assert text is None
    assert len(_Client.instances) == 1
    assert len(_Client.instances[0].calls) == 1
    assert _Client.instances[0].calls[0][1].startswith("https://redirect.example/v1/entries?")


def test_miniflux_link_local_is_rejected_before_a_client(monkeypatch):
    _Client.instances = []
    monkeypatch.setattr("src.pinned_fetch.httpx.AsyncClient", _Client)

    with pytest.raises(PinnedFetchError, match="link-local"):
        asyncio.run(fetch_miniflux_unread(
            "http://169.254.169.254",
            {"X-Auth-Token": "secret-token"},
        ))
    assert _Client.instances == []


def test_miniflux_private_address_follows_the_integration_knob(monkeypatch):
    _Client.instances = []
    monkeypatch.delenv("INTEGRATION_API_BLOCK_PRIVATE_IPS", raising=False)
    monkeypatch.setattr("src.pinned_fetch.httpx.AsyncClient", _Client)

    text = asyncio.run(fetch_miniflux_unread(
        "http://192.168.1.50",
        {"X-Auth-Token": "secret-token"},
    ))
    assert text == "- [News] Hello — https://news.example/hello"
    assert len(_Client.instances) == 1

    _Client.instances = []
    monkeypatch.setenv("INTEGRATION_API_BLOCK_PRIVATE_IPS", "true")
    with pytest.raises(PinnedFetchError):
        asyncio.run(fetch_miniflux_unread(
            "http://192.168.1.50",
            {"X-Auth-Token": "secret-token"},
        ))
    assert _Client.instances == []
