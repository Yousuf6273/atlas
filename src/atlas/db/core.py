"""SQLite connection management and migrations."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any

from atlas.timeutil import now_iso

_MIGRATION_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


def connect(db_path: Path | str, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a connection with the pragmas ATLAS relies on.

    WAL mode lets the Streamlit UI read while a background job writes.
    """
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=check_same_thread, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    if str(db_path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction (connections run in autocommit mode otherwise)."""
    if conn.in_transaction:
        # Nested use: rely on the outer transaction.
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _available_migrations() -> list[tuple[int, str, str]]:
    pkg = resources.files("atlas.db") / "migrations"
    found = []
    for entry in pkg.iterdir():
        m = _MIGRATION_RE.match(entry.name)
        if m:
            found.append((int(m.group(1)), m.group(2), entry.read_text(encoding="utf-8")))
    return sorted(found)


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply pending migrations in order. Returns versions applied."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    newly = []
    for version, name, sql in _available_migrations():
        if version in applied:
            continue
        # executescript commits implicitly; wrap the SQL in its own transaction.
        conn.executescript(f"BEGIN;\n{sql}\nINSERT INTO schema_migrations(version, name, applied_at) VALUES ({version}, '{name}', '{now_iso()}');\nCOMMIT;")
        newly.append(version)
    return newly


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)


def open_db(db_path: Path | str, *, check_same_thread: bool = True) -> sqlite3.Connection:
    conn = connect(db_path, check_same_thread=check_same_thread)
    migrate(conn)
    return conn


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_default)


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _default(o: Any):
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"Not JSON serialisable: {type(o)!r}")


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
