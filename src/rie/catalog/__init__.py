"""The SQLite catalog: connections, migrations and backups.

The catalog lives on the internal disk (`<paths.data>/catalog.db`), never on the photo drive. It runs in WAL mode so
the web server can read while a folder is being analyzed. Migrations are plain SQL files in `migrations/`, applied
in order; `PRAGMA user_version` records the last one applied. Each run backs the catalog up into
`<paths.data>/backups/` (plan §3.5).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

_MIGRATION = re.compile(r"(\d{4})_[a-z0-9_]+\.sql")
BACKUPS_KEPT = 10


class CatalogError(RuntimeError):
    """Raised when the catalog is newer than this code or a migration fails."""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def migrations() -> list[tuple[int, str, str]]:
    """(number, file name, SQL) for every migration shipped with the code, in order."""
    found = []
    for entry in resources.files(__package__).joinpath("migrations").iterdir():
        match = _MIGRATION.fullmatch(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort()
    numbers = [n for n, _, _ in found]
    if numbers != list(range(1, len(numbers) + 1)):
        raise CatalogError(f"migrations must be numbered 0001, 0002, ... without gaps; found {numbers}")
    return found


def connect(path: Path, *, migrate: bool = True, any_thread: bool = False) -> sqlite3.Connection:
    """Open the catalog, creating and migrating it as needed.

    A connection is used by one thread at a time; `any_thread` lets it move between threads, as the web server's
    per-request connections do (FastAPI runs a request's dependency and handler in different worker threads).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # autocommit; transactions are explicit
    conn = sqlite3.connect(path, timeout=30.0, isolation_level=None, check_same_thread=not any_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    if migrate:
        apply_migrations(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def apply_migrations(conn: sqlite3.Connection) -> list[str]:
    """Apply the migrations the catalog has not seen yet; returns their file names."""
    available = migrations()
    current = schema_version(conn)
    if current > len(available):
        raise CatalogError(f"the catalog is at schema {current}, newer than this code ({len(available)})")
    applied = []
    for number, name, sql in available[current:]:
        try:
            conn.execute("BEGIN IMMEDIATE")
            # executescript would commit the open transaction first; run the statements one by one instead.
            for statement in _statements(sql):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {number}")
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            raise CatalogError(f"migration {name} failed: {exc}") from exc
        applied.append(name)
    return applied


def _statements(sql: str) -> Iterator[str]:
    """Split a migration into statements; a statement ends where sqlite3 says it is complete."""
    buffer = ""
    for line in sql.splitlines(keepends=True):
        if not buffer and (not line.strip() or line.lstrip().startswith("--")):
            continue
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer.strip()
            buffer = ""
    if buffer.strip():
        raise CatalogError(f"incomplete SQL statement at the end of a migration: {buffer.strip()[:80]!r}")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A write transaction that takes the write lock up front, so concurrent writers wait instead of failing."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def backup(conn: sqlite3.Connection, data_dir: Path, *, keep: int = BACKUPS_KEPT) -> Path:
    """Copy the catalog into `<data_dir>/backups/`, keeping the newest `keep` copies.

    The backups are the app's own files on the internal disk; pruning them never touches a photo folder.
    """
    folder = data_dir / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    target = folder / f"catalog-{stamp}.db"
    dest = sqlite3.connect(target)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    old = sorted(folder.glob("catalog-*.db"))[:-keep] if keep > 0 else []
    for path in old:
        path.unlink()  # guard allowlist: catalog backups in the data folder
    return target


def dumps(value: Any) -> str:
    """Compact JSON for catalog columns."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def loads(text: str | None, default: Any = None) -> Any:
    if text is None or text == "":
        return default
    return json.loads(text)
