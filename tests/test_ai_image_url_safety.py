"""Provider image URLs are downloaded by this process. The download must use
the addresses that passed the safety check, and must not follow a redirect
until that hop is checked too.
"""
from src import ai_interaction


class _GenerationResponse:
    status_code = 200
    text = ""

    def __init__(self, image_url):
        self._image_url = image_url

    def json(self):
        return {"data": [{"url": self._image_url}]}


class _DownloadResponse:
    status_code = 503
    content = b""
    headers = {}
    url = "https://images.example.com/generated.png?sig=abc"


def _patch_generation(monkeypatch, image_url, *, on_get=None, resolver=None):
    created = []

    class _AsyncClient:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs
            self.gets = []
            created.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json, headers):
            return _GenerationResponse(image_url)

        async def get(self, url, **kwargs):
            self.gets.append(url)
            if on_get is not None:
                return on_get(url)
            return _DownloadResponse()

    import httpx
    import src.settings as settings

    monkeypatch.setattr(settings, "load_settings", lambda: {})
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(
        ai_interaction,
        "_resolve_model",
        lambda model_spec, owner=None: (
            "https://api.openai.example/v1/chat/completions",
            "dall-e-3",
            {"Authorization": "Bearer test"},
        ),
    )
    if resolver is not None:
        monkeypatch.setattr("src.url_safety._default_resolver", resolver)
    return created


async def test_generate_image_pins_provider_download(monkeypatch):
    from src.pinned_fetch import _PinnedAsyncTransport

    provider_url = "https://images.example.com/generated.png?sig=abc"
    created = _patch_generation(
        monkeypatch,
        provider_url,
        resolver=lambda host: ["93.184.216.34"],
    )

    result = await ai_interaction.do_generate_image("draw a chair\ndall-e-3")

    downloads = [client for client in created if client.gets]
    assert result["image_url"] == provider_url
    assert len(downloads) == 1
    client = downloads[0]
    assert client.gets == [provider_url]
    assert client.kwargs.get("follow_redirects") is False
    transport = client.kwargs.get("transport")
    assert isinstance(transport, _PinnedAsyncTransport)
    assert [str(ip) for ip in transport._pinned_ips] == ["93.184.216.34"]


async def test_generate_image_rejects_unsafe_provider_url_without_download(monkeypatch):
    unsafe_url = "http://169.254.169.254/latest/meta-data"
    created = _patch_generation(monkeypatch, unsafe_url)

    result = await ai_interaction.do_generate_image("draw a chair\ndall-e-3")

    assert result["error"].startswith("Image API returned unsafe image URL:")
    assert "169.254.169.254" in result["error"]
    assert all(client.gets == [] for client in created)
