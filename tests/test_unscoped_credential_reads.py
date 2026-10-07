"""Missing-owner lookups must not attach another user's model credentials."""

import asyncio

import pytest

import src.endpoint_resolver as endpoint_resolver
from src.agent_loop import _agent_route_tool_mode
from src.ai_interaction import _resolve_model
from src.chatgpt_subscription import (
    ChatGPTSubscriptionAuthNotFound,
    resolve_runtime_credentials,
)
from src.endpoint_resolver import (
    resolve_endpoint,
    resolve_endpoint_by_id,
    resolve_route_descriptor,
)
from src.owner_identity import scoped_read_allowed
from tests.test_resolve_endpoint_fallbacks import _endpoint, _install_resolver_fakes


def test_scoped_read_requires_an_owner_when_auth_is_on(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    assert scoped_read_allowed(None) is False
    assert scoped_read_allowed("") is False
    assert scoped_read_allowed("alice") is True


def test_scoped_read_allows_a_missing_owner_when_auth_is_off(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    assert scoped_read_allowed(None) is True


def test_resolve_endpoint_does_not_load_a_key_without_an_owner(monkeypatch):
    settings = {
        "default_endpoint_id": "bob-ep",
        "default_model": "bob-chat",
    }
    _install_resolver_fakes(monkeypatch, settings, [_endpoint("bob-ep", "bob-chat")])
    monkeypatch.setenv("AUTH_ENABLED", "true")
    opened = []
    real_session = endpoint_resolver.SessionLocal

    def guarded():
        opened.append("opened")
        return real_session()

    monkeypatch.setattr(endpoint_resolver, "SessionLocal", guarded)

    url, model, headers = resolve_endpoint("default")

    assert opened == []
    assert url is None
    assert model is None
    assert headers is None


def test_resolve_endpoint_by_id_does_not_load_a_key_without_an_owner(monkeypatch):
    _install_resolver_fakes(monkeypatch, {}, [_endpoint("bob-ep", "bob-chat")])
    monkeypatch.setenv("AUTH_ENABLED", "true")
    opened = []
    real_session = endpoint_resolver.SessionLocal

    def guarded():
        opened.append("opened")
        return real_session()

    monkeypatch.setattr(endpoint_resolver, "SessionLocal", guarded)

    assert resolve_endpoint_by_id("bob-ep", "bob-chat") is None
    assert opened == []


def test_route_descriptor_does_not_scan_endpoints_without_an_owner(monkeypatch):
    _install_resolver_fakes(monkeypatch, {}, [_endpoint("bob-ep", "bob-chat")])
    monkeypatch.setenv("AUTH_ENABLED", "true")
    opened = []
    real_session = endpoint_resolver.SessionLocal

    def guarded():
        opened.append("opened")
        return real_session()

    monkeypatch.setattr(endpoint_resolver, "SessionLocal", guarded)

    assert resolve_route_descriptor(
        "https://bob-ep.example/v1/chat/completions",
        "bob-chat",
        {"Authorization": "Bearer key-bob-ep"},
    ) == {
        "endpoint_id": None,
        "endpoint_label": "Selected route",
        "endpoint_cost_tracked": True,
    }
    assert opened == []


def test_auth_off_still_resolves_the_single_user_endpoint(monkeypatch):
    settings = {
        "default_endpoint_id": "local",
        "default_model": "local-chat",
    }
    _install_resolver_fakes(monkeypatch, settings, [_endpoint("local", "local-chat")])

    url, model, headers = resolve_endpoint("default")

    assert url == "https://local.example/v1/chat/completions"
    assert model == "local-chat"
    assert headers == {"Authorization": "Bearer key-local"}


def test_subscription_credentials_are_not_loaded_without_an_owner(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")

    def boom():
        raise AssertionError("provider auth database was opened")

    monkeypatch.setattr("src.chatgpt_subscription._database_handles", boom)

    with pytest.raises(ChatGPTSubscriptionAuthNotFound):
        resolve_runtime_credentials("auth-bob", owner=None)


def test_resolve_model_does_not_search_every_endpoint_without_an_owner(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    opened = []

    def boom():
        opened.append("opened")
        raise AssertionError("endpoint database was opened")

    monkeypatch.setattr("src.database.SessionLocal", boom)

    with pytest.raises(ValueError, match="No enabled endpoints found"):
        _resolve_model("bob-secret-model")
    assert opened == []


def test_list_models_does_not_call_other_users_endpoints_without_an_owner(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    opened = []

    def boom():
        opened.append("opened")
        raise AssertionError("endpoint database was opened")

    monkeypatch.setattr("src.database.SessionLocal", boom)

    from src.agent_tools.model_interaction_tools import list_models

    result = asyncio.run(list_models(""))

    assert result == {"results": "No enabled model endpoints configured."}
    assert opened == []


def test_tool_mode_does_not_read_endpoint_keys_without_an_owner(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    opened = []

    def boom():
        opened.append("opened")
        raise AssertionError("endpoint database was opened")

    monkeypatch.setattr("core.database.SessionLocal", boom)

    assert _agent_route_tool_mode("https://api.example/v1", "gpt-4")[0] is True
    assert opened == []


def test_email_ai_draft_resolves_models_for_the_mcp_owner(monkeypatch):
    import mcp_servers.email_server as email_server

    seen = []

    monkeypatch.setattr(
        email_server,
        "_read_email",
        lambda **kwargs: {
            "from_address": "pat@example.com",
            "from": "Pat <pat@example.com>",
            "subject": "Hello",
            "body": "Please reply",
            "message_id": "<m@example.com>",
        },
    )
    monkeypatch.setattr(email_server, "_load_email_writing_style", lambda account=None: "")

    def fake_resolve(kind, owner=None, **kwargs):
        seen.append(("resolve", kind, owner))
        return None, None, None

    def fake_fallbacks(owner=None):
        seen.append(("fallbacks", owner))
        return []

    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint", fake_resolve)
    monkeypatch.setattr(
        "src.endpoint_resolver.resolve_utility_fallback_candidates",
        fake_fallbacks,
    )

    token = email_server._CURRENT_OWNER.set("alice")
    try:
        result = asyncio.run(email_server._ai_draft_reply_to_email(uid="1"))
    finally:
        email_server._CURRENT_OWNER.reset(token)

    assert result == {"error": "No LLM endpoint configured for AI reply"}
    assert ("resolve", "utility", "alice") in seen
    assert ("resolve", "default", "alice") in seen
    assert ("fallbacks", "alice") in seen
