"""Manual review queue (uncertain matches, borderline qualifications) and evaluation reviews."""

from __future__ import annotations

import sqlite3
from typing import Any

from atlas.db.core import dumps, loads, rows_to_dicts
from atlas.timeutil import now_iso


def enqueue(conn: sqlite3.Connection, *, item_type: str, company_id: int, reason: str, other_company_id: int | None = None,
            score: float | None = None, details: dict[str, Any] | None = None) -> None:
    """Add (or refresh) an open review item. Resolved items are not re-opened."""
    conn.execute(
        "INSERT INTO review_queue(item_type, company_id, other_company_id, score, reason, details_json, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'open', ?) "
        "ON CONFLICT(item_type, company_id, COALESCE(other_company_id, 0)) DO UPDATE SET "
        "score = excluded.score, reason = excluded.reason, details_json = excluded.details_json "
        "WHERE review_queue.status = 'open'",
        (item_type, company_id, other_company_id, score, reason, dumps(details or {}), now_iso()),
    )


def resolve(conn: sqlite3.Connection, review_id: int, *, resolution: str, status: str = "resolved") -> None:
    conn.execute(
        "UPDATE review_queue SET status = ?, resolution = ?, resolved_at = ? WHERE id = ?",
        (status, resolution, now_iso(), review_id),
    )


def open_items(conn: sqlite3.Connection, item_type: str | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT r.*, a.name AS company_name, a.domain AS company_domain, b.name AS other_name, b.domain AS other_domain "
        "FROM review_queue r JOIN companies a ON a.id = r.company_id LEFT JOIN companies b ON b.id = r.other_company_id "
        "WHERE r.status = 'open' AND a.merged_into_id IS NULL AND (b.id IS NULL OR b.merged_into_id IS NULL)"
    )
    params: list[Any] = []
    if item_type:
        sql += " AND r.item_type = ?"
        params.append(item_type)
    sql += " ORDER BY r.id"
    rows = rows_to_dicts(conn.execute(sql, params).fetchall())
    for r in rows:
        r["details"] = loads(r.pop("details_json"), {})
    return rows


def count_open(conn: sqlite3.Connection) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM review_queue r JOIN companies a ON a.id = r.company_id "
        "WHERE r.status = 'open' AND a.merged_into_id IS NULL"
    ).fetchone()[0])


# ---------------------------------------------------------------- evaluation

EVAL_FIELDS = ("discovery_relevant", "is_duplicate", "industry_correct", "contact_source_valid", "unsupported_claims",
               "claims_checked", "internship_assessment_correct", "outreach_useful", "notes")


def save_evaluation(conn: sqlite3.Connection, company_id: int, job_id: int | None, values: dict[str, Any]) -> None:
    clean = {k: values.get(k) for k in EVAL_FIELDS}
    for k in ("discovery_relevant", "is_duplicate", "industry_correct", "contact_source_valid",
              "internship_assessment_correct", "outreach_useful"):
        if clean[k] is not None:
            clean[k] = int(bool(clean[k]))
    conn.execute(
        "INSERT INTO evaluation_reviews(company_id, job_id, discovery_relevant, is_duplicate, industry_correct, "
        "contact_source_valid, unsupported_claims, claims_checked, internship_assessment_correct, outreach_useful, notes, "
        "reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(company_id) DO UPDATE SET "
        "job_id = excluded.job_id, discovery_relevant = excluded.discovery_relevant, is_duplicate = excluded.is_duplicate, "
        "industry_correct = excluded.industry_correct, contact_source_valid = excluded.contact_source_valid, "
        "unsupported_claims = excluded.unsupported_claims, claims_checked = excluded.claims_checked, "
        "internship_assessment_correct = excluded.internship_assessment_correct, outreach_useful = excluded.outreach_useful, "
        "notes = excluded.notes, reviewed_at = excluded.reviewed_at",
        (company_id, job_id, clean["discovery_relevant"], clean["is_duplicate"], clean["industry_correct"],
         clean["contact_source_valid"], clean["unsupported_claims"], clean["claims_checked"],
         clean["internship_assessment_correct"], clean["outreach_useful"], clean["notes"], now_iso()),
    )


def evaluations(conn: sqlite3.Connection, job_id: int | None = None) -> list[dict[str, Any]]:
    sql = "SELECT e.*, c.name AS company_name FROM evaluation_reviews e JOIN companies c ON c.id = e.company_id"
    params: list[Any] = []
    if job_id is not None:
        sql += " WHERE e.job_id = ?"
        params.append(job_id)
    return rows_to_dicts(conn.execute(sql + " ORDER BY e.id", params).fetchall())
