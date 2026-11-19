import sqlite3

import pytest

from rie import catalog


def test_connect_creates_the_schema_once(tmp_path):
    path = tmp_path / "data" / "catalog.db"
    conn = catalog.connect(path)
    version = catalog.schema_version(conn)
    assert version == len(catalog.migrations()) >= 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"folders", "images", "decisions", "suggestions", "trash_entries", "sidecars", "trust"} <= tables
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()
    again = catalog.connect(path)
    assert catalog.apply_migrations(again) == []
    assert catalog.schema_version(again) == version


def test_trust_dial_starts_with_technical_and_duplicates_automatic(tmp_path):
    conn = catalog.connect(tmp_path / "c.db")
    modes = dict(conn.execute("SELECT kind, mode FROM trust").fetchall())
    assert modes == {
        "technical": "automatic",
        "duplicates": "automatic",
        "eyes": "suggest",
        "expression": "suggest",
        "composition": "suggest",
    }


def test_a_newer_catalog_is_refused(tmp_path):
    path = tmp_path / "c.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 999")
    conn.close()
    with pytest.raises(catalog.CatalogError, match="newer than this code"):
        catalog.connect(path)


def test_transaction_rolls_back_on_error(tmp_path):
    conn = catalog.connect(tmp_path / "c.db")
    with pytest.raises(RuntimeError), catalog.transaction(conn):
        conn.execute("INSERT INTO folders (path, name, opened_at) VALUES ('/x', 'x', 'now')")
        raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 0


def test_backup_keeps_the_newest_copies(tmp_path):
    conn = catalog.connect(tmp_path / "c.db")
    conn.execute("INSERT INTO folders (path, name, opened_at) VALUES ('/x', 'x', 'now')")
    made = [catalog.backup(conn, tmp_path, keep=3) for _ in range(5)]
    kept = sorted((tmp_path / "backups").glob("catalog-*.db"))
    assert kept == made[-3:]
    copy = sqlite3.connect(kept[-1])
    assert copy.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 1


def test_statements_split_on_complete_sql():
    sql = "-- comment\nCREATE TABLE a (x TEXT DEFAULT ';');\n\nINSERT INTO a VALUES ('1;2');\n"
    assert list(catalog._statements(sql)) == ["CREATE TABLE a (x TEXT DEFAULT ';');", "INSERT INTO a VALUES ('1;2');"]
