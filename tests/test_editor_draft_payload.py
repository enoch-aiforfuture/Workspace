import sys
import types
from unittest.mock import MagicMock


def _load_module(monkeypatch):
    db_stub = types.ModuleType("core.database")
    db_stub.EditorDraft = MagicMock()
    db_stub.SessionLocal = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", db_stub)
    # delitem(raising=False) does not undo a later import when the module
    # was absent. setitem records NOTSET for a missing key, so teardown
    # deletes the stubbed route module instead of leaving MagicMock models
    # bound for the rest of the suite.
    name = "routes.editor_draft_routes"
    if name in sys.modules:
        monkeypatch.delitem(sys.modules, name)
    else:
        monkeypatch.setitem(sys.modules, name, None)
        sys.modules.pop(name, None)

    import routes.editor_draft_routes as mod

    return mod


def test_load_payload_rejects_non_object_json(monkeypatch):
    mod = _load_module(monkeypatch)

    assert mod._load_payload("[]") == {}
    assert mod._load_payload('"draft"') == {}
    assert mod._load_payload("{bad json") == {}
    assert mod._load_payload('{"layers": []}') == {"layers": []}
