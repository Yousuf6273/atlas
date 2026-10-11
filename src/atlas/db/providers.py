"""Discovery-provider metadata and health bookkeeping."""

from __future__ import annotations

import sqlite3
from typing import Any

from atlas.db.core import rows_to_dicts
from atlas.timeutil import now_iso


def register_provider(conn: sqlite3.Connection, *, name: str, display_name: str, availability: str, description: str,
                      coverage: str, source_url: str, limitations: str, enabled_default: bool) -> None:
    """Insert or refresh static metadata. Never overwrites the user's enabled flag."""
    conn.execute(
        "INSERT INTO discovery_providers(name, display_name, enabled, availability, description, coverage, source_url, "
        "limitations, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET display_name = excluded.display_name, availability = excluded.availability, "
        "description = excluded.description, coverage = excluded.coverage, source_url = excluded.source_url, "
        "limitations = excluded.limitations, updated_at = excluded.updated_at",
        (name, display_name, int(enabled_default), availability, description, coverage, source_url, limitations, now_iso()),
    )


def set_enabled(conn: sqlite3.Connection, name: str, enabled: bool) -> None:
    conn.execute("UPDATE discovery_providers SET enabled = ?, updated_at = ? WHERE name = ?", (int(enabled), now_iso(), name))


def is_enabled(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT enabled FROM discovery_providers WHERE name = ?", (name,)).fetchone()
    return bool(row and row["enabled"])


def record_run(conn: sqlite3.Connection, name: str, *, ok: bool, result_count: int = 0, error: str | None = None) -> None:
    now = now_iso()
    if ok:
        conn.execute(
            "UPDATE discovery_providers SET last_run_at = ?, last_success_at = ?, last_result_count = ?, "
            "total_runs = total_runs + 1, total_results = total_results + ?, updated_at = ? WHERE name = ?",
            (now, now, result_count, result_count, now, name),
        )
    else:
        conn.execute(
            "UPDATE discovery_providers SET last_run_at = ?, last_error = ?, last_error_at = ?, last_result_count = ?, "
            "total_runs = total_runs + 1, updated_at = ? WHERE name = ?",
            (now, (error or "unknown error")[:2000], now, result_count, now, name),
        )


def record_health(conn: sqlite3.Connection, name: str, health: str) -> None:
    conn.execute(
        "UPDATE discovery_providers SET last_health = ?, last_health_at = ?, updated_at = ? WHERE name = ?",
        (health[:1000], now_iso(), now_iso(), name),
    )


def list_providers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows_to_dicts(conn.execute("SELECT * FROM discovery_providers ORDER BY availability, name").fetchall())
