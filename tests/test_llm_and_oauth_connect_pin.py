"""Chat and OAuth connects must use the address the safety check allowed.

The shared LLM client is reused across hosts, so it cannot carry one IP
list from the moment it is built. Each new connection has to check again.
The Google token exchange sends a client secret and must not re-resolve.
"""
import asyncio
import json
from pathlib import Path

import httpcore
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.pinned_fetch import _CheckingAsyncBackend


class _Socket:
    async def connect_tcp(self, host, port, timeout, local_address, socket_options):
        self.calls.append((host, port))
        return object()

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AssertionError("unix socket")

    async def sleep(self, seconds):
        return None


def _backend(monkeypatch, answers):
    seen = []

    def _resolver(host):
        seen.append(host)
        return list(answers)

    monkeypatch.setattr("src.url_safety._default_resolver", _resolver)
    backend = _CheckingAsyncBackend()
    real = _Socket()
    real.calls = []
    backend._real = real
    return backend, real, seen


def test_shared_client_connects_to_the_checked_address(monkeypatch):
    backend, real, seen = _backend(monkeypatch, ["93.184.216.34"])

    asyncio.run(backend.connect_tcp("api.example", 443, timeout=1))

    assert seen == ["api.example"]
    assert real.calls == [("93.184.216.34", 443)]


def test_shared_client_rejects_a_link_local_answer(monkeypatch):
    backend, real, _seen = _backend(monkeypatch, ["169.254.169.254"])

    with pytest.raises(httpcore.ConnectError, match="link-local"):
        asyncio.run(backend.connect_tcp("rebind.example", 443, timeout=1))

    assert real.calls == []


def test_shared_client_still_allows_a_local_model_server(monkeypatch):
    backend, real, _seen = _backend(monkeypatch, ["127.0.0.1"])

    asyncio.run(backend.connect_tcp("localhost", 11434, timeout=1))

    assert real.calls == [("127.0.0.1", 11434)]


def test_llm_http_client_pins_new_connections_and_ignores_redirects():
    import src.llm_core as llm_core

    previous = llm_core._http_client
    llm_core._http_client = None
    client = llm_core._get_http_client()
    try:
        assert client.follow_redirects is False
        backend = client._transport._pool._network_backend
        assert isinstance(backend, _CheckingAsyncBackend)
        assert backend._block_private is False
    finally:
        asyncio.run(client.aclose())
        llm_core._http_client = previous


def test_google_token_exchange_pins_the_client_secret(monkeypatch, tmp_path):
    import routes.mcp.mcp_routes as mcp_routes

    keys = tmp_path / "keys.json"
    token = tmp_path / "token.json"
    keys.write_text(json.dumps({
        "installed": {"client_id": "client-id", "client_secret": "super-secret"},
    }), encoding="utf-8")

    class _Srv:
        id = "s1"
        oauth_config = json.dumps({
            "keys_file": str(keys),
            "token_file": str(token),
        })
        args = "[]"
        env = "{}"
        name = "gmail"
        transport = "stdio"
        command = "npx"
        url = ""

    class _Query:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return _Srv()

    class _DB:
        def query(self, _model):
            return _Query()

        def close(self):
            return None

    seen = []

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"access_token": "access", "refresh_token": "refresh"}

    async def fake_request(method, url, **kwargs):
        seen.append((method, url, kwargs))
        return _Resp()

    monkeypatch.setattr(mcp_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(mcp_routes, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(mcp_routes, "_sanitize_mcp_oauth_config", lambda cfg: cfg)
    monkeypatch.setattr("src.pinned_fetch.arequest_pinned", fake_request)
    manager = MagicMock()
    manager.connect_server = AsyncMock(return_value=True)
    manager.get_server_status = MagicMock(return_value={"tool_count": 2})
    router = mcp_routes.setup_mcp_routes(manager)
    endpoint = [r for r in router.routes if getattr(r, "name", None) == "oauth_exchange"][-1].endpoint

    result = asyncio.run(endpoint(
        "s1",
        None,
        "https://app.example/callback?code=auth-code",
    ))

    assert result.status_code == 200
    assert "Authorization Successful" in result.body.decode()
    method, url, kwargs = seen[0]
    assert method == "POST"
    assert url == "https://oauth2.googleapis.com/token"
    assert kwargs["block_private"] is True
    assert kwargs["data"]["client_secret"] == "super-secret"
    assert kwargs["data"]["code"] == "auth-code"
    saved = json.loads(Path(token).read_text(encoding="utf-8"))
    assert saved["access_token"] == "access"
