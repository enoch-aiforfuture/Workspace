"""PersonalDocsManager must not extract files until something reads the index."""

import json

from src import personal_docs


def test_index_is_not_built_during_init(tmp_path, monkeypatch):
    personal = tmp_path / "personal"
    personal.mkdir()
    (personal / "note.md").write_text("hello personal")
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "other.md").write_text("there")
    (personal / "indexed_directories.json").write_text(json.dumps([str(extra)]))

    calls = {"n": 0}
    real = personal_docs.load_personal_index

    def wrapped(directory, *args, **kwargs):
        calls["n"] += 1
        return real(directory, *args, **kwargs)

    monkeypatch.setattr(personal_docs, "load_personal_index", wrapped)

    mgr = personal_docs.PersonalDocsManager(str(personal))

    assert calls["n"] == 0
    assert mgr._index_ready is False

    names = {item["name"] for item in mgr.index}

    assert calls["n"] == 2
    assert "note.md" in names
    assert "extra/other.md" in names
    assert mgr._index_ready is True

    again = mgr.index
    assert calls["n"] == 2
    assert again is mgr.index


def test_explicit_refresh_rebuilds_after_a_new_file(tmp_path):
    personal = tmp_path / "personal"
    personal.mkdir()
    mgr = personal_docs.PersonalDocsManager(str(personal))
    assert mgr.index == []

    (personal / "later.md").write_text("added after init")
    mgr.refresh_index()

    assert [item["name"] for item in mgr.index] == ["later.md"]
