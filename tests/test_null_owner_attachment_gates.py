"""Null-owner rows are not shared attachments or upload sessions."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_gallery_attachment_rejects_null_and_other_owners():
    from routes.email_routes import _reject_foreign_workspace_item

    with pytest.raises(HTTPException) as null_owner:
        _reject_foreign_workspace_item(None, "gallery", SimpleNamespace(owner=None), "alice")
    assert null_owner.value.status_code == 404

    with pytest.raises(HTTPException) as other:
        _reject_foreign_workspace_item(None, "gallery", SimpleNamespace(owner="bob"), "alice")
    assert other.value.status_code == 404

    _reject_foreign_workspace_item(None, "gallery", SimpleNamespace(owner="alice"), "alice")
    _reject_foreign_workspace_item(None, "gallery", SimpleNamespace(owner="bob"), "")


def test_document_attachment_rejects_another_owner():
    from routes.email_routes import _reject_foreign_workspace_item

    with pytest.raises(HTTPException) as exc:
        _reject_foreign_workspace_item(None, "document", SimpleNamespace(owner="bob"), "alice")
    assert exc.value.status_code == 404

    _reject_foreign_workspace_item(None, "document", SimpleNamespace(owner="alice"), "alice")


def test_upload_session_null_owner_is_not_shared():
    from routes.upload_routes import upload_session_belongs_to_owner

    assert upload_session_belongs_to_owner(None, None) is True
    assert upload_session_belongs_to_owner("alice", "") is True
    assert upload_session_belongs_to_owner("alice", "alice") is True
    assert upload_session_belongs_to_owner(None, "alice") is False
    assert upload_session_belongs_to_owner("bob", "alice") is False
