"""Chat FTS startup backfill must not scan the index once per message.

message_id is an UNINDEXED FTS5 column. A correlated NOT EXISTS over it is
O(n^2) even when every row is already indexed. The migration uses one
non-correlated id set instead.
"""
import sqlite3
import time


def _prepare(db_path, rows, indexed_ids):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE chat_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL
        )
        """
    )
    conn.executemany(
        "INSERT INTO chat_messages(id, session_id, role, content) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()

    from core import database as cdb

    # Build the table and triggers, then replace the full backfill with the
    # rows this case wants already indexed.
    cdb._migrate_chat_messages_fts()
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM chat_messages_fts")
    if indexed_ids:
        conn.executemany(
            """
            INSERT INTO chat_messages_fts(content, message_id, session_id, role)
            SELECT content, id, session_id, role FROM chat_messages WHERE id = ?
            """,
            [(mid,) for mid in indexed_ids],
        )
    conn.commit()
    conn.close()


def test_partial_backfill_restores_missing_rows_once(tmp_path, monkeypatch):
    from core import database as cdb

    db_path = tmp_path / "app.db"
    monkeypatch.setattr(cdb, "DATABASE_URL", f"sqlite:///{db_path}")
    _prepare(
        db_path,
        [
            ("m1", "s1", "user", "alpha transcript"),
            ("m2", "s1", "assistant", "beta transcript"),
            ("m3", "s1", "user", "gamma transcript"),
        ],
        ["m1"],
    )

    cdb._migrate_chat_messages_fts()
    cdb._migrate_chat_messages_fts()

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT message_id FROM chat_messages_fts ORDER BY message_id"
        ).fetchall()
        assert rows == [("m1",), ("m2",), ("m3",)]
    finally:
        conn.close()


def test_null_fts_message_id_does_not_block_backfill(tmp_path, monkeypatch):
    from core import database as cdb

    db_path = tmp_path / "app.db"
    monkeypatch.setattr(cdb, "DATABASE_URL", f"sqlite:///{db_path}")
    _prepare(
        db_path,
        [
            ("present", "s1", "user", "already indexed"),
            ("missing", "s1", "user", "needs backfill"),
        ],
        ["present"],
    )
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO chat_messages_fts(content, message_id, session_id, role)
        VALUES ('orphan', NULL, 's1', 'user')
        """
    )
    conn.commit()
    conn.close()

    cdb._migrate_chat_messages_fts()

    conn = sqlite3.connect(db_path)
    try:
        present = conn.execute(
            "SELECT COUNT(*) FROM chat_messages_fts WHERE message_id = 'present'"
        ).fetchone()[0]
        missing = conn.execute(
            "SELECT content FROM chat_messages_fts WHERE message_id = 'missing'"
        ).fetchone()
        assert present == 1
        assert missing == ("needs backfill",)
    finally:
        conn.close()


def test_complete_index_backfill_stays_cheap(tmp_path, monkeypatch):
    from core import database as cdb

    db_path = tmp_path / "app.db"
    monkeypatch.setattr(cdb, "DATABASE_URL", f"sqlite:///{db_path}")
    body = "word " * 40
    rows = [(f"m{i}", "s1", "user", f"{i} {body}") for i in range(4000)]
    _prepare(db_path, rows, [])
    # _prepare cleared the index. Fill it completely, then time a no-op pass.
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO chat_messages_fts(content, message_id, session_id, role)
        SELECT content, id, session_id, role FROM chat_messages
        """
    )
    conn.commit()
    conn.close()

    started = time.perf_counter()
    cdb._migrate_chat_messages_fts()
    elapsed = time.perf_counter() - started

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM chat_messages_fts").fetchone()[0]
    finally:
        conn.close()
    assert count == 4000
    assert elapsed < 0.75, f"complete FTS backfill took {elapsed:.2f}s"
