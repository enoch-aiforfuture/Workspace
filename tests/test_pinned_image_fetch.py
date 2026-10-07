"""Image-result downloads must connect to the safety check's address snapshot.

A later DNS answer, or a redirect, must not move the socket onto a link-local
address the check already rejected.
"""
import ipaddress

import pytest

from src.pinned_fetch import (
    PinnedFetchError,
    _PinnedAsyncBackend,
    aget_pinned,
    resolve_pinned_ips,
)

PUBLIC = ipaddress.ip_address("93.184.216.34")


async def test_resolve_snapshot_is_the_only_connect_destination(monkeypatch):
    answers = iter([[str(PUBLIC)], ["169.254.169.254"]])
    hosts = []

    def _flip(host):
        hosts.append(host)
        return next(answers)

    monkeypatch.setattr("src.url_safety._default_resolver", _flip)
    pinned = resolve_pinned_ips("https://rebind.example/img.png")

    connected = []

    class _Recording:
        async def connect_tcp(self, host, port, timeout, local_address, socket_options):
            connected.append((host, port))
            return object()

    backend = _PinnedAsyncBackend(pinned)
    backend._real = _Recording()
    await backend.connect_tcp("rebind.example", 443, timeout=1.0)

    assert hosts == ["rebind.example"]
    assert pinned == [PUBLIC]
    assert connected == [(str(PUBLIC), 443)]


def test_link_local_result_url_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "src.url_safety._default_resolver",
        lambda host: ["169.254.169.254"],
    )
    with pytest.raises(PinnedFetchError, match="link-local"):
        resolve_pinned_ips("http://169.254.169.254/latest/meta-data")


async def test_redirect_to_link_local_is_not_fetched(monkeypatch):
    fetched = []
    client_kwargs = []

    class _Resp:
        def __init__(self, url):
            self.status_code = 302
            self.headers = {"location": "http://169.254.169.254/latest/meta-data"}
            self.url = url
            self.content = b""

    class _Client:
        def __init__(self, *args, **kwargs):
            client_kwargs.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            fetched.append(url)
            if "169.254.169.254" in url:
                raise AssertionError("redirect target must not be fetched")
            return _Resp(url)

    def _resolver(host):
        if host == "169.254.169.254":
            return ["169.254.169.254"]
        return [str(PUBLIC)]

    monkeypatch.setattr("src.url_safety._default_resolver", _resolver)
    monkeypatch.setattr("src.pinned_fetch.httpx.AsyncClient", _Client)

    with pytest.raises(PinnedFetchError, match="link-local"):
        await aget_pinned("https://cdn.example/img.png")

    assert fetched == ["https://cdn.example/img.png"]
    assert client_kwargs[0]["follow_redirects"] is False
