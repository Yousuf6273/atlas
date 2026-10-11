"""Search jobs, filters, progress events and provider runs."""

from __future__ import annotations

import sqlite3
from typing import Any

from atlas.db.core import dumps, loads, rows_to_dicts, transaction
from atlas.models.enums import JobStatus
from atlas.models.search import ParsedQuery, SearchCriteria
from atlas.timeutil import now_iso, older_than


def create_job(conn: sqlite3.Connection, parsed: ParsedQuery) -> int:
    c = parsed.criteria
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO search_jobs(query_text, criteria_json, parser_method, status, target_count, created_at, stage, message) "
            "VALUES (?, ?, ?, 'pending', ?, ?, 'queued', 'Waiting to start')",
            (c.raw_query, c.model_dump_json(), parsed.method, c.desired_count, now_iso()),
        )
        job_id = int(cur.lastrowid)
        filters: list[tuple[str, str]] = []
        filters += [("industry", i.value) for i in c.industries]
        filters += [("excluded_industry", i.value) for i in c.excluded_industries]
        filters += [("area", a.key) for a in c.areas]
        filters += [("keyword", k) for k in c.keywords]
        filters += [("excluded_keyword", k) for k in c.excluded_keywords]
        filters += [("size", s.value) for s in c.size_preference]
        filters += [("skill", s) for s in c.skills]
        filters += [("task_preference", s) for s in c.task_preferences]
        filters += [("excluded_business_type", b.value) for b in c.excluded_business_types]
        filters += [("contact_role", r.value) for r in c.contact_roles]
        filters += [("internship_type", c.internship_type), ("desired_count", str(c.desired_count))]
        filters += [("min_quality", str(c.min_quality))]
        conn.executemany(
            "INSERT OR IGNORE INTO search_filters(job_id, filter_key, filter_value) VALUES (?, ?, ?)",
            [(job_id, k, v) for k, v in filters],
        )
    return job_id


def get_job(conn: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM search_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def job_criteria(conn: sqlite3.Connection, job_id: int) -> SearchCriteria:
    row = conn.execute("SELECT criteria_json FROM search_jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise KeyError(f"Job {job_id} not found")
    return SearchCriteria.model_validate_json(row["criteria_json"])


def list_jobs(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    return rows_to_dicts(conn.execute("SELECT * FROM search_jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall())


def set_status(conn: sqlite3.Connection, job_id: int, status: JobStatus, *, error: str | None = None, message: str | None = None) -> None:
    now = now_iso()
    sets = ["status = ?", "heartbeat_at = ?"]
    params: list[Any] = [status.value, now]
    if status == JobStatus.RUNNING:
        sets.append("started_at = COALESCE(started_at, ?)")
        params.append(now)
        sets.append("cancel_requested = 0")
    if status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED):
        sets.append("finished_at = ?")
        params.append(now)
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    if message is not None:
        sets.append("message = ?")
        params.append(message)
    params.append(job_id)
    conn.execute(f"UPDATE search_jobs SET {', '.join(sets)} WHERE id = ?", params)


def update_progress(conn: sqlite3.Connection, job_id: int, *, stage: str | None = None, done: int | None = None,
                    total: int | None = None, message: str | None = None) -> None:
    sets = ["heartbeat_at = ?"]
    params: list[Any] = [now_iso()]
    for col, val in (("stage", stage), ("progress_done", done), ("progress_total", total), ("message", message)):
        if val is not None:
            sets.append(f"{col} = ?")
            params.append(val)
    params.append(job_id)
    conn.execute(f"UPDATE search_jobs SET {', '.join(sets)} WHERE id = ?", params)


def set_stats(conn: sqlite3.Connection, job_id: int, stats: dict[str, Any]) -> None:
    conn.execute("UPDATE search_jobs SET stats_json = ? WHERE id = ?", (dumps(stats), job_id))


def job_stats(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT stats_json FROM search_jobs WHERE id = ?", (job_id,)).fetchone()
    return loads(row["stats_json"], {}) if row else {}


def add_event(conn: sqlite3.Connection, job_id: int, message: str, *, level: str = "info", stage: str | None = None,
              data: dict[str, Any] | None = None) -> None:
    conn.execute(
        "INSERT INTO job_events(job_id, ts, level, stage, message, data_json) VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, now_iso(), level, stage, message, dumps(data) if data else None),
    )


def list_events(conn: sqlite3.Connection, job_id: int, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM job_events WHERE job_id = ? ORDER BY id DESC LIMIT ?", (job_id, limit)
    ).fetchall()
    return rows_to_dicts(rows)


def request_cancel(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("UPDATE search_jobs SET cancel_requested = 1 WHERE id = ?", (job_id,))


def cancel_requested(conn: sqlite3.Connection, job_id: int) -> bool:
    row = conn.execute("SELECT cancel_requested FROM search_jobs WHERE id = ?", (job_id,)).fetchone()
    return bool(row and row["cancel_requested"])


def mark_stale_jobs_interrupted(conn: sqlite3.Connection, stale_after_s: float = 120.0, alive_ids: set[int] | None = None) -> list[int]:
    """Jobs left 'running' by a crashed/stopped process become 'interrupted' (resumable)."""
    alive_ids = alive_ids or set()
    stale = []
    for row in conn.execute("SELECT id, heartbeat_at FROM search_jobs WHERE status = 'running'").fetchall():
        if row["id"] in alive_ids:
            continue
        if older_than(row["heartbeat_at"], seconds=stale_after_s):
            stale.append(int(row["id"]))
    for jid in stale:
        set_status(conn, jid, JobStatus.INTERRUPTED, message="Interrupted (process stopped). Resume to continue.")
        add_event(conn, jid, "Job marked interrupted: no heartbeat from a running worker", level="warning")
    return stale


# ---------------------------------------------------------------- provider runs

def start_provider_run(conn: sqlite3.Connection, job_id: int, provider: str) -> None:
    conn.execute(
        "INSERT INTO provider_runs(job_id, provider, status, started_at) VALUES (?, ?, 'running', ?) "
        "ON CONFLICT(job_id, provider) DO UPDATE SET status = 'running', started_at = excluded.started_at, "
        "finished_at = NULL, error = NULL",
        (job_id, provider, now_iso()),
    )


def finish_provider_run(conn: sqlite3.Connection, job_id: int, provider: str, *, status: str, result_count: int = 0,
                        error: str | None = None, notes: str | None = None, partial: bool = False) -> None:
    conn.execute(
        "UPDATE provider_runs SET status = ?, finished_at = ?, result_count = ?, error = ?, notes = ?, partial = ? "
        "WHERE job_id = ? AND provider = ?",
        (status, now_iso(), result_count, error, notes, int(partial), job_id, provider),
    )


def provider_runs(conn: sqlite3.Connection, job_id: int) -> list[dict[str, Any]]:
    return rows_to_dicts(conn.execute("SELECT * FROM provider_runs WHERE job_id = ? ORDER BY id", (job_id,)).fetchall())


def completed_providers(conn: sqlite3.Connection, job_id: int) -> set[str]:
    rows = conn.execute(
        "SELECT provider FROM provider_runs WHERE job_id = ? AND status IN ('completed','skipped') AND partial = 0",
        (job_id,)
    ).fetchall()
    return {r["provider"] for r in rows}
