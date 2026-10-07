"""Catalog fetches and token-bearing probes must not re-resolve DNS or
follow a redirect onto a different host.
"""
import asyncio
from types import SimpleNamespace

import pytest

from routes.auth_routes import SESSION_COOKIE, setup_auth_routes
from routes.cookbook_routes import _vllm_recipe_repo
from src.pinned_fetch import _PinnedAsyncTransport, aget_pinned
from tests.test_api_chat_security import (
    _DB,
    _Endpoint,
    _ModelEndpoint,
    _Request,
    _SessionManager,
    _install_sync_chat_stubs,
    _load_webhook_routes_for_test,
    _sync_chat_endpoint,
)


class _Resp:
    status_code = 200
    is_success = True
    text = ""
    content = b""
    headers = {}

    def __init__(self, url="https://example.test"):
        self.url = url

    def json(self):
        return {"data": [{"id": "discovered-model"}]}

    def raise_for_status(self):
        return None


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

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        resp = _Resp(url)
        if kwargs.get("headers"):
            resp.status_code = 302
            resp.headers = {"location": "http://169.254.169.254/latest/meta-data"}
        return resp


def test_vllm_recipe_repo_rejects_path_and_host_escapes():
    assert _vllm_recipe_repo("MiniMaxAI/MiniMax-M2") == "MiniMaxAI/MiniMax-M2"
    assert _vllm_recipe_repo(" org/name ") == "org/name"
    for bad in (
        "../secret",
        "org/../../etc/passwd",
        "org/name/extra",
        "org/name?x=1",
        "org/name#frag",
        "user@host/name",
        "https://evil.example/x",
        "org\\name",
        "",
        "onlyone",
    ):
        assert _vllm_recipe_repo(bad) is None


def test_pinned_get_drops_headers_before_a_redirect(monkeypatch):
    _Client.instances = []

    def _resolver(host):
        if host == "169.254.169.254":
            return ["169.254.169.254"]
        return ["93.184.216.34"]

    monkeypatch.setattr("src.url_safety._default_resolver", _resolver)
    monkeypatch.setattr("src.pinned_fetch.httpx.AsyncClient", _Client)

    with pytest.raises(Exception, match="link-local|rejected|private|metadata"):
        asyncio.run(aget_pinned(
            "https://catalog.example/library",
            headers={"User-Agent": "workspace-cookbook/1.0", "Authorization": "Bearer secret"},
            timeout=5,
        ))

    assert len(_Client.instances) == 1
    pinned = _Client.instances[0]
    assert pinned.kwargs["follow_redirects"] is False
    assert isinstance(pinned.kwargs["transport"], _PinnedAsyncTransport)
    assert len(pinned.calls) == 1
    url, kwargs = pinned.calls[0]
    assert url == "https://catalog.example/library"
    assert kwargs["headers"]["Authorization"] == "Bearer secret"
    assert "169.254.169.254" not in url


@pytest.mark.asyncio
async def test_api_chat_auto_model_fetch_sends_the_key_on_a_pinned_request(monkeypatch):
    webhook_routes = _load_webhook_routes_for_test(monkeypatch)
    _install_sync_chat_stubs(monkeypatch)
    local_endpoint = _Endpoint(
        owner=None,
        base_url="http://localhost:11434/v1",
        api_key="configured-key",
    )
    monkeypatch.setattr(webhook_routes, "ModelEndpoint", _ModelEndpoint)
    monkeypatch.setattr(webhook_routes, "SessionLocal", lambda: _DB([local_endpoint]))
    monkeypatch.setattr(
        webhook_routes,
        "validate_public_http_url",
        lambda url, *, max_length=2048: (_ for _ in ()).throw(
            AssertionError("configured fallback endpoint should not be publicly validated")
        ),
    )
    seen = []

    async def fake_request(method, url, **kwargs):
        seen.append((method, url, kwargs))
        return _Resp()

    monkeypatch.setattr("src.pinned_fetch.arequest_pinned", fake_request)
    monkeypatch.setattr(
        "routes.webhook.webhook_routes.httpx.AsyncClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("plain client")),
    )

    session_manager = _SessionManager()
    sync_chat = _sync_chat_endpoint(webhook_routes, session_manager)
    body = SimpleNamespace(
        message="hello",
        model="auto",
        api_key=None,
        base_url=None,
        provider=None,
        session=None,
    )

    response = await sync_chat(_Request(owner=None), body)

    assert response["model"] == "discovered-model"
    assert seen[0][0] == "GET"
    assert seen[0][1] == "http://localhost:11434/v1/models"
    assert seen[0][2]["headers"]["Authorization"] == "Bearer configured-key"


def _auth_tester(monkeypatch, integration):
    monkeypatch.setattr("routes.auth_routes.get_integration", lambda _id: integration)
    monkeypatch.setattr(
        "routes.auth_routes._load_settings",
        lambda: {"reminder_ntfy_topic": "reminders"},
    )

    class _Auth:
        def get_username_for_token(self, token):
            return "admin" if token == "tok" else None

        def is_admin(self, user):
            return user == "admin"

    router = setup_auth_routes(_Auth())
    endpoint = next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", "").endswith("/integrations/{integration_id}/test")
    )
    request = SimpleNamespace(cookies={SESSION_COOKIE: "tok"})
    return endpoint, request


def test_ntfy_connectivity_test_pins_the_bearer_token(monkeypatch):
    seen = []

    async def fake_request(method, url, **kwargs):
        seen.append((method, url, kwargs))
        return _Resp()

    monkeypatch.setattr("src.pinned_fetch.arequest_pinned", fake_request)
    endpoint, request = _auth_tester(monkeypatch, {
        "preset": "ntfy",
        "base_url": "https://ntfy.example/ignored",
        "api_key": "secret-token",
        "auth_type": "bearer",
    })

    result = asyncio.run(endpoint("ntfy-1", request))

    assert result["ok"] is True
    method, url, kwargs = seen[0]
    assert method == "POST"
    assert url == "https://ntfy.example/reminders"
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
    assert kwargs["content"].startswith("Connectivity test")


def test_discord_webhook_test_pins_and_blocks_private_targets(monkeypatch):
    seen = []

    async def fake_request(method, url, **kwargs):
        seen.append((method, url, kwargs))
        return _Resp()

    monkeypatch.setattr("src.pinned_fetch.arequest_pinned", fake_request)
    endpoint, request = _auth_tester(monkeypatch, {
        "preset": "discord_webhook",
        "base_url": "https://discord.com/api/webhooks/1/secret",
    })

    result = asyncio.run(endpoint("discord-1", request))

    assert result["ok"] is True
    method, url, kwargs = seen[0]
    assert method == "POST"
    assert url == "https://discord.com/api/webhooks/1/secret"
    assert kwargs["block_private"] is True
    assert kwargs["json"]["embeds"][0]["title"] == "Workspace connectivity test"
