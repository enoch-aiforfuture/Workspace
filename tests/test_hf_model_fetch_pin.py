"""Hugging Face lookups send the stored token. The connect must use the
address snapshot from the safety check and must not follow a redirect.
"""
import asyncio

import routes.cookbook_routes as cookbook_routes
from src.pinned_fetch import _PinnedAsyncTransport
from src.tools.cookbook import _cookbook_hf_model_info


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = b"{}" if status_code == 200 else b""

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
        return _Resp(200, {"siblings": [{"rfilename": "model.gguf"}, {"rfilename": "readme.md"}]})


def _hf_gguf_endpoint():
    router = cookbook_routes.setup_cookbook_routes()
    for route in router.routes:
        if getattr(route, "path", "") == "/api/cookbook/hf-gguf-files":
            return route.endpoint
    raise AssertionError("GET /api/cookbook/hf-gguf-files route not found")


def test_hf_model_info_is_pinned_and_sends_the_token(monkeypatch):
    _Client.instances = []
    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["93.184.216.34"])
    monkeypatch.setattr("routes.cookbook_helpers.load_stored_hf_token", lambda **kwargs: "hf_secret")
    monkeypatch.setattr("src.pinned_fetch.httpx.AsyncClient", _Client)

    result = asyncio.run(_cookbook_hf_model_info("org/model"))

    assert result["siblings"] == ["model.gguf", "readme.md"]
    assert len(_Client.instances) == 1
    pinned = _Client.instances[0]
    assert pinned.kwargs["follow_redirects"] is False
    transport = pinned.kwargs["transport"]
    assert isinstance(transport, _PinnedAsyncTransport)
    assert [str(ip) for ip in transport._pinned_ips] == ["93.184.216.34"]
    method, url, headers = pinned.calls[0]
    assert method == "GET"
    assert url == "https://huggingface.co/api/models/org/model"
    assert headers["Authorization"] == "Bearer hf_secret"
    assert len(pinned.calls) == 1


def test_hf_model_info_does_not_follow_a_redirect(monkeypatch):
    _Client.instances = []
    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["93.184.216.34"])
    monkeypatch.setattr("routes.cookbook_helpers.load_stored_hf_token", lambda **kwargs: "hf_secret")
    monkeypatch.setattr("src.pinned_fetch.httpx.AsyncClient", _Client)

    result = asyncio.run(_cookbook_hf_model_info("org/redirect"))

    assert "302" in result["error"]
    assert len(_Client.instances) == 1
    assert len(_Client.instances[0].calls) == 1
    assert "169.254.169.254" not in _Client.instances[0].calls[0][1]


def test_hf_model_info_rejects_a_link_local_answer(monkeypatch):
    _Client.instances = []
    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["169.254.169.254"])
    monkeypatch.setattr("routes.cookbook_helpers.load_stored_hf_token", lambda **kwargs: "hf_secret")
    monkeypatch.setattr("src.pinned_fetch.httpx.AsyncClient", _Client)

    result = asyncio.run(_cookbook_hf_model_info("org/model"))

    assert "link-local" in result["error"]
    assert _Client.instances == []


def test_hf_gguf_listing_is_pinned(monkeypatch):
    _Client.instances = []
    endpoint = _hf_gguf_endpoint()
    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["93.184.216.34"])
    monkeypatch.setattr("routes.cookbook_routes.load_stored_hf_token", lambda **kwargs: "hf_secret")
    monkeypatch.setattr("src.pinned_fetch.httpx.AsyncClient", _Client)

    result = asyncio.run(endpoint("org/model", owner="alice"))

    assert result["ok"] is True
    assert result["files"] == ["model.gguf"]
    assert len(_Client.instances) == 1
    pinned = _Client.instances[0]
    assert pinned.kwargs["follow_redirects"] is False
    assert [str(ip) for ip in pinned.kwargs["transport"]._pinned_ips] == ["93.184.216.34"]
    method, url, headers = pinned.calls[0]
    assert method == "GET"
    assert url == "https://huggingface.co/api/models/org/model"
    assert headers["Authorization"] == "Bearer hf_secret"
