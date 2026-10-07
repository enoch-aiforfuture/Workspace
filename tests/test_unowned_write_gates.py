"""Writes must not store a new row when the request has no identity."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


def _request(user, *, host="203.0.113.7"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user, api_token=False),
        client=SimpleNamespace(host=host),
        app=SimpleNamespace(state=SimpleNamespace(
            auth_manager=SimpleNamespace(is_configured=True),
        )),
        query_params={},
        headers={},
    )


def test_upload_owner_rejects_a_missing_identity(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)
    from routes.upload_routes import upload_owner

    with pytest.raises(HTTPException) as exc:
        upload_owner(_request(None))
    assert exc.value.status_code == 401


def test_upload_owner_auth_off_is_the_single_user_library(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    from routes.upload_routes import upload_owner

    assert upload_owner(_request(None)) is None


def test_openai_session_requires_an_identity(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)
    import routes.session_routes as session_routes

    manager = MagicMock()
    router = session_routes.setup_session_routes(manager, {"OPENAI_API_KEY": "sk-test"})
    matches = [
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == "/api/session/openai"
        and "POST" in getattr(route, "methods", set())
    ]
    with pytest.raises(HTTPException) as exc:
        matches[-1](_request(None))
    assert exc.value.status_code == 401
    manager.create_session.assert_not_called()


def test_fixture_message_is_not_shared_when_unowned():
    from routes.email_helpers import fixture_message_visible

    assert fixture_message_visible("", None) is True
    assert fixture_message_visible("bob", "") is True
    assert fixture_message_visible("alice", "alice") is True
    assert fixture_message_visible("", "alice") is False
    assert fixture_message_visible("bob", "alice") is False
