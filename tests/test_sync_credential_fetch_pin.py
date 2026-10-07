"""Sync credential fetches must connect to the checked address.

Model probes, ChatGPT token refresh, and the Google mailbox refresh all
used to call httpx directly. A second DNS answer could move the API key
or refresh token onto a link-local address.
"""
import pytest

from src.pinned_fetch import PinnedFetchError, _PinnedTransport, sync_post


class _Resp:
    status_code = 200
    text = ""
    is_success = True

    def json(self):
        return {"access_token": "new-access", "expires_in": 3600}

    def raise_for_status(self):
        return None


def test_sync_post_connects_to_the_checked_address_and_keeps_the_body(monkeypatch):
    seen = {}

    class _Client:
        def __init__(self, *args, **kwargs):
            seen["follow_redirects"] = kwargs.get("follow_redirects")
            seen["transport"] = kwargs.get("transport")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def request(self, method, url, **kwargs):
            seen["call"] = (method, url, kwargs)
            return _Resp()

    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["93.184.216.34"])
    monkeypatch.setattr("src.pinned_fetch.httpx.Client", _Client)

    sync_post(
        "https://oauth2.googleapis.com/token",
        data={"refresh_token": "stored-refresh", "client_secret": "secret"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
        block_private=True,
        verify=True,
    )

    assert seen["follow_redirects"] is False
    assert isinstance(seen["transport"], _PinnedTransport)
    assert seen["transport"]._pinned_ips
    method, url, kwargs = seen["call"]
    assert method == "POST"
    assert url == "https://oauth2.googleapis.com/token"
    assert kwargs["data"]["refresh_token"] == "stored-refresh"
    assert kwargs["data"]["client_secret"] == "secret"


def test_sync_post_rejects_a_link_local_token_host(monkeypatch):
    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["169.254.169.254"])

    with pytest.raises(PinnedFetchError, match="link-local"):
        sync_post(
            "https://oauth2.googleapis.com/token",
            data={"client_secret": "secret"},
            block_private=True,
            timeout=5,
        )


def test_chatgpt_refresh_sends_the_refresh_token_on_a_pinned_post(monkeypatch):
    from src.chatgpt_subscription import refresh_oauth_tokens

    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return _Resp()

    monkeypatch.setattr("src.chatgpt_subscription._sync_post", fake_post)
    data = refresh_oauth_tokens("old-access", "stored-refresh")

    assert data["access_token"] == "new-access"
    assert seen["kwargs"]["block_private"] is True
    assert seen["kwargs"]["data"]["refresh_token"] == "stored-refresh"


def test_google_refresh_pins_the_client_secret(monkeypatch):
    from routes.email_helpers import _refresh_google_token

    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return _Resp()

    class _Row:
        oauth_refresh_token = "encrypted"
        oauth_access_token = ""
        oauth_token_expiry = ""

    class _DB:
        def get(self, _model, _account_id):
            return _Row()

        def commit(self):
            return None

        def close(self):
            return None

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setattr("src.pinned_fetch.sync_post", fake_post)
    monkeypatch.setattr("core.database.SessionLocal", lambda: _DB())
    monkeypatch.setattr("src.secret_storage.decrypt", lambda value: "stored-refresh")
    monkeypatch.setattr("src.secret_storage.encrypt", lambda value: "enc-" + value)

    # The helper imports SessionLocal and decrypt inside the function.
    import core.database as database
    import src.secret_storage as secret_storage
    monkeypatch.setattr(database, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(secret_storage, "decrypt", lambda value: "stored-refresh")
    monkeypatch.setattr(secret_storage, "encrypt", lambda value: "enc-" + value)

    token = _refresh_google_token("acct")

    assert token == "new-access"
    assert seen["url"] == "https://oauth2.googleapis.com/token"
    assert seen["kwargs"]["block_private"] is True
    assert seen["kwargs"]["data"]["client_secret"] == "secret"
    assert seen["kwargs"]["data"]["refresh_token"] == "stored-refresh"
