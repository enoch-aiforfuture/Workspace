"""File symlinks and tracked directory symlinks must not leak outside content.

os.walk does not follow directory symlinks, but opening a file symlink reads
the target. A notes file inside the personal-docs tree that points at another
user's file or /etc was indexed. os.walk also enters its start path, so a
tracked directory that is a symlink out of the tree was walked too.
"""
import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from src.index_walk import path_stays_inside
from src.personal_docs import PersonalDocsManager, load_personal_index
import src.rag_vector as rag_vector

SECRET = "SUPERSECRET_OUTSIDE_MARKER"


def _chunks(records):
    return "\n".join(chunk for rec in records for chunk in rec.get("chunks", []))


def test_path_stays_inside_rejects_symlink_escape(tmp_path):
    base = tmp_path / "personal"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = base / "escape"
    os.symlink(outside, link)
    inside = base / "notes"
    inside.mkdir()

    assert path_stays_inside(str(inside), str(base)) is True
    assert path_stays_inside(str(link), str(base)) is False


def test_keyword_index_skips_file_symlink_to_outside(tmp_path):
    personal = tmp_path / "personal"
    personal.mkdir()
    (personal / "keep.md").write_text("keep-me")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_text(SECRET)
    os.symlink(secret, personal / "alias.md")
    # A symlink that stays inside the tree is still not followed. The real
    # file is indexed under its own name.
    os.symlink(personal / "keep.md", personal / "keep-link.md")

    records = load_personal_index(str(personal))
    names = {rec["name"] for rec in records}

    assert names == {"keep.md"}
    assert SECRET not in _chunks(records)


def test_keyword_index_does_not_follow_directory_symlink(tmp_path):
    personal = tmp_path / "personal"
    personal.mkdir()
    (personal / "keep.md").write_text("keep-me")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text(SECRET)
    os.symlink(outside, personal / "escape")

    records = load_personal_index(str(personal))

    assert {rec["name"] for rec in records} == {"keep.md"}
    assert SECRET not in _chunks(records)


def test_refresh_skips_tracked_directory_symlink(tmp_path):
    personal = tmp_path / "personal"
    personal.mkdir()
    (personal / "keep.md").write_text("keep-me")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text(SECRET)
    link = personal / "escape"
    os.symlink(outside, link)

    mgr = PersonalDocsManager(str(personal))
    mgr.indexed_directories = [str(link)]
    mgr.refresh_index()

    blob = json.dumps(mgr.index)
    assert "keep.md" in {item["name"] for item in mgr.index}
    assert SECRET not in blob
    assert "secret.md" not in blob


def test_index_all_directories_skips_tracked_directory_symlink(tmp_path):
    personal = tmp_path / "personal"
    personal.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text(SECRET)
    link = personal / "escape"
    os.symlink(outside, link)

    class _Rag:
        def __init__(self):
            self.calls = []

        def index_personal_documents(self, directory, file_extensions=None, owner=None):
            self.calls.append(directory)
            return {"success": True, "indexed_count": 0}

    rag = _Rag()
    mgr = PersonalDocsManager(str(personal), rag_manager=rag)
    mgr.indexed_directories = [str(link)]

    result = mgr.index_all_directories()

    assert result["failed"] == 1
    assert result["success"] == 1
    assert rag.calls == [str(personal)]


def test_vector_index_skips_file_symlink_to_outside(tmp_path):
    personal = tmp_path / "personal"
    personal.mkdir()
    (personal / "keep.md").write_text("keep-me")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_text(SECRET)
    os.symlink(secret, personal / "alias.md")

    recorded = set()
    rag = rag_vector.VectorRAG.__new__(rag_vector.VectorRAG)

    def _record(text, metadata):
        recorded.add(metadata["source"])
        assert SECRET not in text
        return True

    rag.add_document = _record
    result = rag.index_personal_documents(str(personal))

    assert result["success"] is True
    indexed = {os.path.relpath(p, str(personal)) for p in recorded}
    assert indexed == {"keep.md"}
