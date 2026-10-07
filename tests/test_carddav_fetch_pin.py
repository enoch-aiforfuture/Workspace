"""CardDAV sends a username and password. The connect must use the address
snapshot from the safety check and must not follow a redirect.
"""
import routes.contacts_routes as contacts
from src.pinned_fetch import _PinnedTransport


class _Redirect:
    status_code = 302
    text = ""
    headers = {"location": "http://169.254.169.254/latest/meta-data"}


class _Created:
    status_code = 201
    text = ""
    headers = {}


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

    def request(self, method, url, headers=None, json=None, content=None, auth=None):
        self.calls.append((method, url, headers, content, auth))
        if method == "PUT":
            return _Created()
        return _Redirect()


def _reset_cache():
    contacts._contact_cache["contacts"] = []
    contacts._contact_cache["fetched_at"] = None


def test_carddav_fetch_pins_basic_auth_and_ignores_redirect(monkeypatch):
    _Client.instances = []
    _reset_cache()
    monkeypatch.delenv("CARDDAV_BLOCK_PRIVATE_IPS", raising=False)
    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(
        contacts,
        "_get_carddav_config",
        lambda: {
            "url": "https://dav.example/books/alice",
            "username": "alice",
            "password": "s3cret",
        },
    )
    monkeypatch.setattr("src.pinned_fetch.httpx.Client", _Client)

    result = contacts._fetch_contacts(force=True)

    assert result == []
    assert len(_Client.instances) == 2
    methods = []
    for pinned in _Client.instances:
        assert pinned.kwargs["follow_redirects"] is False
        transport = pinned.kwargs["transport"]
        assert isinstance(transport, _PinnedTransport)
        assert [str(ip) for ip in transport._pinned_ips] == ["93.184.216.34"]
        assert len(pinned.calls) == 1
        method, url, headers, content, auth = pinned.calls[0]
        methods.append(method)
        assert url == "https://dav.example/books/alice"
        assert "169.254.169.254" not in url
        assert auth == ("alice", "s3cret")
    assert methods == ["REPORT", "GET"]
    report_content = _Client.instances[0].calls[0][3]
    assert b"addressbook-query" in report_content


def test_carddav_create_pins_put(monkeypatch):
    _Client.instances = []
    monkeypatch.delenv("CARDDAV_BLOCK_PRIVATE_IPS", raising=False)
    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(
        contacts,
        "_get_carddav_config",
        lambda: {
            "url": "https://dav.example/books/alice",
            "username": "alice",
            "password": "s3cret",
        },
    )
    monkeypatch.setattr("src.pinned_fetch.httpx.Client", _Client)

    assert contacts._create_contact("Ada", email="ada@example.com") is True
    assert len(_Client.instances) == 1
    pinned = _Client.instances[0]
    assert pinned.kwargs["follow_redirects"] is False
    assert [str(ip) for ip in pinned.kwargs["transport"]._pinned_ips] == ["93.184.216.34"]
    method, url, headers, content, auth = pinned.calls[0]
    assert method == "PUT"
    assert url.startswith("https://dav.example/books/alice/")
    assert url.endswith(".vcf")
    assert auth == ("alice", "s3cret")
    assert b"BEGIN:VCARD" in content


def test_carddav_fetch_rejects_link_local_before_sending_password(monkeypatch):
    _Client.instances = []
    _reset_cache()
    monkeypatch.setattr(
        contacts,
        "_get_carddav_config",
        lambda: {
            "url": "http://169.254.169.254/latest/meta-data",
            "username": "alice",
            "password": "s3cret",
        },
    )
    monkeypatch.setattr("src.pinned_fetch.httpx.Client", _Client)

    assert contacts._fetch_contacts(force=True) == []
    assert _Client.instances == []
