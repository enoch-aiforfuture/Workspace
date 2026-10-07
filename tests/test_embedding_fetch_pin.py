"""Embedding calls send a bearer token. The connect must use the address
snapshot from the safety check and must not follow a redirect.
"""
from src.embeddings import EmbeddingClient
from src.pinned_fetch import PinnedFetchError, _PinnedTransport


class _Resp:
    status_code = 200

    def json(self):
        return {"data": [{"embedding": [0.5, 0.25], "index": 0}]}

    def raise_for_status(self):
        return None


class _Client:
    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        _Client.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def request(self, method, url, headers=None, json=None, content=None):
        self.calls.append((method, url, headers, json))
        return _Resp()


def test_embedding_post_is_pinned_to_the_checked_address(monkeypatch):
    _Client.instances = []
    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["93.184.216.34"])
    client = EmbeddingClient(
        url="http://embed.example/v1/embeddings",
        model="embed-test",
        api_key="secret-key",
    )
    # Patch after construction so the long-lived client stays a real httpx
    # client (the production branch) while the pinned request uses the fake.
    monkeypatch.setattr("src.pinned_fetch.httpx.Client", _Client)
    vecs = client.encode(["hello"], normalize_embeddings=False)

    assert vecs.tolist() == [[0.5, 0.25]]
    assert len(_Client.instances) == 1
    pinned = _Client.instances[0]
    assert pinned.kwargs["follow_redirects"] is False
    transport = pinned.kwargs["transport"]
    assert isinstance(transport, _PinnedTransport)
    assert [str(ip) for ip in transport._pinned_ips] == ["93.184.216.34"]
    method, url, headers, body = pinned.calls[0]
    assert method == "POST"
    assert url == "http://embed.example/v1/embeddings"
    assert headers["Authorization"] == "Bearer secret-key"
    assert body["input"] == ["hello"]


def test_embedding_post_rejects_link_local_before_sending_the_key(monkeypatch):
    _Client.instances = []
    client = EmbeddingClient(
        url="http://169.254.169.254/latest/meta-data",
        model="embed-test",
        api_key="secret-key",
    )
    monkeypatch.setattr("src.pinned_fetch.httpx.Client", _Client)
    try:
        client.encode(["hello"])
    except PinnedFetchError as exc:
        assert "link-local" in str(exc)
    else:
        raise AssertionError("link-local embedding URL must be rejected")
    assert _Client.instances == []
