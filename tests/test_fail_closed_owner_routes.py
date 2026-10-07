"""Owner routes must fail closed when the request has no identity.

Signatures, editor drafts, preferences, comparisons, and the session list
treated a missing user as auth-disabled single-user mode: every row, and for
sessions the incognito purge and endpoint lookup were unscoped too.
"""
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import Comparison, EditorDraft, Session as DbSession, Signature


def _request(user, *, api_token=False, host="203.0.113.7"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user, api_token=api_token),
        client=SimpleNamespace(host=host),
        app=SimpleNamespace(state=SimpleNamespace(
            auth_manager=SimpleNamespace(is_configured=True),
        )),
        query_params={},
        headers={},
    )


def _endpoint(router, method, path):
    matches = [
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set())
    ]
    if not matches:
        raise RuntimeError(f"{method} {path} not found")
    return matches[-1]


@pytest.fixture
def db(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'owners.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()
    try:
        session.add(Signature(id="sig-alice", owner="alice", name="Alice", data_png="YWxpY2U="))
        session.add(Signature(id="sig-bob", owner="bob", name="Bob", data_png="Ym9i"))
        session.add(EditorDraft(
            id="draft-alice", owner="alice", name="Alice draft", payload="{}", is_active=True,
        ))
        session.add(EditorDraft(
            id="draft-bob", owner="bob", name="Bob draft", payload="{}", is_active=True,
        ))
        session.add(Comparison(
            id="cmp-alice", owner="alice", prompt="alice prompt",
            model_a="a", model_b="b", endpoint_a="http://a", endpoint_b="http://b",
        ))
        session.add(Comparison(
            id="cmp-bob", owner="bob", prompt="bob prompt",
            model_a="a", model_b="b", endpoint_a="http://a", endpoint_b="http://b",
        ))
        session.commit()
    finally:
        session.close()
    return factory


def _bind(monkeypatch, module, factory):
    monkeypatch.setattr(module, "SessionLocal", factory)


def _real_routes():
    """Drop route modules an earlier test rebound to a stubbed database."""
    import importlib

    import routes.compare_routes as compare_routes
    import routes.editor_draft_routes as editor_draft_routes
    import routes.signature_routes as signature_routes

    for module, attr, real in (
        (editor_draft_routes, "EditorDraft", cdb.EditorDraft),
        (signature_routes, "Signature", cdb.Signature),
        (compare_routes, "Comparison", cdb.Comparison),
    ):
        if getattr(module, attr, None) is not real:
            importlib.reload(module)
    return signature_routes, editor_draft_routes, compare_routes


@pytest.mark.asyncio
async def test_no_identity_cannot_read_signatures_drafts_prefs_or_comparisons(monkeypatch, db):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)
    signature_routes, editor_draft_routes, compare_routes = _real_routes()
    import routes.prefs_routes as prefs_routes

    _bind(monkeypatch, signature_routes, db)
    _bind(monkeypatch, editor_draft_routes, db)
    _bind(monkeypatch, compare_routes, db)
    monkeypatch.setattr(prefs_routes, "_load", lambda: pytest.fail("prefs were read"))
    anon = _request(None)

    with pytest.raises(HTTPException) as sigs:
        await _endpoint(signature_routes.setup_signature_routes(), "GET", "/api/signatures")(anon)
    assert sigs.value.status_code == 401

    with pytest.raises(HTTPException) as drafts:
        await _endpoint(editor_draft_routes.setup_editor_draft_routes(), "GET", "/api/editor-drafts")(anon)
    assert drafts.value.status_code == 401

    with pytest.raises(HTTPException) as prefs:
        await _endpoint(prefs_routes.setup_prefs_routes(), "GET", "/api/prefs")(anon)
    assert prefs.value.status_code == 401

    with pytest.raises(HTTPException) as comps:
        _endpoint(compare_routes.setup_compare_routes(MagicMock()), "GET", "/api/compare/history")(anon)
    assert comps.value.status_code == 401

    session = db()
    try:
        assert session.query(Signature).count() == 2
        assert session.query(EditorDraft).count() == 2
        assert session.query(Comparison).count() == 2
    finally:
        session.close()


@pytest.mark.asyncio
async def test_auth_disabled_lists_the_single_user_library(monkeypatch, db):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    signature_routes, editor_draft_routes, compare_routes = _real_routes()

    _bind(monkeypatch, signature_routes, db)
    _bind(monkeypatch, editor_draft_routes, db)
    _bind(monkeypatch, compare_routes, db)
    anon = _request(None)

    sigs = await _endpoint(signature_routes.setup_signature_routes(), "GET", "/api/signatures")(anon)
    assert {row["id"] for row in sigs["signatures"]} == {"sig-alice", "sig-bob"}

    drafts = await _endpoint(editor_draft_routes.setup_editor_draft_routes(), "GET", "/api/editor-drafts")(anon)
    assert {row["id"] for row in drafts["drafts"]} == {"draft-alice", "draft-bob"}

    comps = _endpoint(compare_routes.setup_compare_routes(MagicMock()), "GET", "/api/compare/history")(anon)
    assert {row["id"] for row in comps} == {"cmp-alice", "cmp-bob"}


def test_named_user_cannot_delete_another_signature(monkeypatch, db):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    signature_routes, _, _ = _real_routes()

    _bind(monkeypatch, signature_routes, db)
    delete = _endpoint(signature_routes.setup_signature_routes(), "DELETE", "/api/signatures/{sig_id}")

    import asyncio
    with pytest.raises(HTTPException) as exc:
        asyncio.run(delete("sig-bob", _request("alice")))
    assert exc.value.status_code == 403
    session = db()
    try:
        assert session.query(Signature).filter(Signature.id == "sig-bob").one()
    finally:
        session.close()


def test_no_identity_cannot_list_sessions(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)
    import routes.session_routes as session_routes

    sm = MagicMock()
    router = session_routes.setup_session_routes(sm, {})
    with pytest.raises(HTTPException) as exc:
        _endpoint(router, "GET", "/api/sessions")(_request(None))
    assert exc.value.status_code == 401
    sm.get_sessions_for_user.assert_not_called()


def test_session_list_incognito_purge_stays_on_the_caller(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    import routes.session_routes as session_routes

    engine = create_engine(
        f"sqlite:///{tmp_path / 'sessions.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(session_routes, "SessionLocal", factory)
    old = cdb.utcnow_naive() - timedelta(hours=1)
    session = factory()
    try:
        for owner in ("alice", "bob"):
            session.add(DbSession(
                id=str(uuid.uuid4()),
                owner=owner,
                name="Nobody",
                endpoint_url="http://localhost",
                model="gpt-4",
                archived=False,
                created_at=old,
                updated_at=old,
            ))
        session.commit()
    finally:
        session.close()

    sm = MagicMock()
    sm.get_sessions_for_user.return_value = {}
    router = session_routes.setup_session_routes(sm, {})
    _endpoint(router, "GET", "/api/sessions")(_request("alice"))

    session = factory()
    try:
        left = session.query(DbSession).all()
        assert [row.owner for row in left] == ["bob"]
    finally:
        session.close()
