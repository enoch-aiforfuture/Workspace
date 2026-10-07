"""Gallery and image-edit posts carry an API key. The connect must use the
address snapshot from the safety check, including multipart edit bodies.
"""
import asyncio
from pathlib import Path

from src.pinned_fetch import _PinnedAsyncTransport, arequest_pinned


class _Response:
    status_code = 200
    headers = {}
    content = b""
    text = ""

    def json(self):
        return {}


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

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _Response()


def test_multipart_image_post_is_pinned_and_forwards_files(monkeypatch):
    _Client.instances = []
    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["93.184.216.34"])
    monkeypatch.setattr("src.pinned_fetch.httpx.AsyncClient", _Client)

    response = asyncio.run(arequest_pinned(
        "POST",
        "https://images.example/v1/images/edits",
        headers={"Authorization": "Bearer image-key"},
        data={"prompt": "make it blue", "model": "edit"},
        files={"image": ("source.png", b"png-bytes", "image/png")},
        timeout=30,
    ))

    assert response.status_code == 200
    assert len(_Client.instances) == 1
    pinned = _Client.instances[0]
    assert pinned.kwargs["follow_redirects"] is False
    transport = pinned.kwargs["transport"]
    assert isinstance(transport, _PinnedAsyncTransport)
    assert [str(ip) for ip in transport._pinned_ips] == ["93.184.216.34"]
    method, url, kwargs = pinned.calls[0]
    assert method == "POST"
    assert url == "https://images.example/v1/images/edits"
    assert kwargs["headers"]["Authorization"] == "Bearer image-key"
    assert kwargs["data"]["prompt"] == "make it blue"
    assert kwargs["files"]["image"][0] == "source.png"
    assert "json" in kwargs


def test_image_proxy_posts_do_not_build_a_plain_client():
    gallery = Path("routes/gallery/gallery_routes.py").read_text()
    edit = Path("src/ai_interaction.py").read_text()
    mcp = Path("mcp_servers/image_gen_server.py").read_text()
    assert "httpx.AsyncClient" not in gallery
    assert "httpx.Client(" not in gallery
    assert "httpx.AsyncClient" not in edit
    assert "arequest_pinned(" in edit
    assert "httpx.AsyncClient" not in mcp
    assert "arequest_pinned(" in mcp
    for name in (
        "gallery_ai_upscale",
        "gallery_style_transfer",
        "inpaint_proxy",
        "harmonize_image",
        "ai_tag_image",
    ):
        assert name in gallery
    assert "_post_pinned_gallery(" in gallery
