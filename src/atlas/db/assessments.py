"""Versioned assessments: qualification, internship, tasks and scores.

Saving a new assessment retires (is_current = 0) the previous one instead of
deleting it, so the history of every judgement is retained.

Qualification and scores depend on the search criteria (industries, area), so
they are current *per search job*: assessing a company for search #2 does not
overwrite its verdict for search #1. ``job_id=None`` reads the most recent one.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from atlas.db.core import dumps, loads, transaction
from atlas.models.assessment import (
    InternshipAssessment,
    QualificationResult,
    ScoreBreakdown,
    TaskOpportunity,
)
from atlas.timeutil import now_iso


def save_qualification(conn: sqlite3.Connection, company_id: int, job_id: int | None, q: QualificationResult) -> int:
    with transaction(conn):
        conn.execute("UPDATE qualification_assessments SET is_current = 0 WHERE company_id = ? AND job_id IS ?",
                     (company_id, job_id))
        cur = conn.execute(
            "INSERT INTO qualification_assessments(company_id, job_id, status, target_relevance, primary_industry, "
            "business_type, geo_status, size_band, operational_status, result_json, method, is_current, assessed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (company_id, job_id, q.status.value, q.target_relevance, q.primary_industry, q.business_type.value,
             q.geo_status.value, q.size_band.value, q.operational_status.value, q.model_dump_json(), q.method, now_iso()),
        )
    return int(cur.lastrowid)


def current_qualification(conn: sqlite3.Connection, company_id: int, job_id: int | None = None
                          ) -> QualificationResult | None:
    row = None
    if job_id is not None:
        row = conn.execute(
            "SELECT result_json FROM qualification_assessments WHERE company_id = ? AND job_id = ? AND is_current = 1 "
            "ORDER BY id DESC LIMIT 1", (company_id, job_id)).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT result_json FROM qualification_assessments WHERE company_id = ? AND is_current = 1 "
            "ORDER BY id DESC LIMIT 1", (company_id,)).fetchone()
    return QualificationResult.model_validate_json(row["result_json"]) if row else None


def save_internship(conn: sqlite3.Connection, company_id: int, a: InternshipAssessment) -> int:
    with transaction(conn):
        conn.execute("UPDATE internship_assessments SET is_current = 0 WHERE company_id = ?", (company_id,))
        cur = conn.execute(
            "INSERT INTO internship_assessments(company_id, category, result_json, method, is_current, assessed_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (company_id, a.category.value, a.model_dump_json(), a.method, now_iso()),
        )
    return int(cur.lastrowid)


def current_internship(conn: sqlite3.Connection, company_id: int) -> InternshipAssessment | None:
    row = conn.execute(
        "SELECT result_json FROM internship_assessments WHERE company_id = ? AND is_current = 1 ORDER BY id DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    return InternshipAssessment.model_validate_json(row["result_json"]) if row else None


def save_tasks(conn: sqlite3.Connection, company_id: int, tasks: list[TaskOpportunity]) -> None:
    now = now_iso()
    with transaction(conn):
        conn.execute("UPDATE task_opportunities SET is_current = 0 WHERE company_id = ?", (company_id,))
        conn.executemany(
            "INSERT INTO task_opportunities(company_id, template_key, title, basis, confidence, skill_match, result_json, "
            "is_current, assessed_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
            [(company_id, t.template_key, t.title, t.basis.value, t.confidence.value, t.skill_match, t.model_dump_json(), now)
             for t in tasks],
        )


def current_tasks(conn: sqlite3.Connection, company_id: int) -> list[TaskOpportunity]:
    rows = conn.execute(
        "SELECT result_json FROM task_opportunities WHERE company_id = ? AND is_current = 1 ORDER BY id", (company_id,)
    ).fetchall()
    return [TaskOpportunity.model_validate_json(r["result_json"]) for r in rows]


def save_score(conn: sqlite3.Connection, company_id: int, job_id: int | None, s: ScoreBreakdown,
               weights: dict[str, float]) -> int:
    f = {x.key: x.score for x in s.factors}
    with transaction(conn):
        conn.execute("UPDATE prospect_scores SET is_current = 0 WHERE company_id = ? AND job_id IS ?", (company_id, job_id))
        cur = conn.execute(
            "INSERT INTO prospect_scores(company_id, job_id, total, industry, tasks, internship, contact, geography, activity, "
            "suitability, evidence_confidence, outreach_readiness, readiness_label, weights_json, breakdown_json, "
            "is_current, scored_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (company_id, job_id, s.total, f.get("industry", 0), f.get("tasks", 0), f.get("internship", 0),
             f.get("contact", 0), f.get("geography", 0), f.get("activity", 0), s.suitability, s.evidence_confidence,
             s.outreach_readiness, s.readiness_label, dumps(weights), s.model_dump_json(), now_iso()),
        )
    return int(cur.lastrowid)


def current_score(conn: sqlite3.Connection, company_id: int, job_id: int | None = None) -> ScoreBreakdown | None:
    row = None
    if job_id is not None:
        row = conn.execute(
            "SELECT breakdown_json FROM prospect_scores WHERE company_id = ? AND job_id = ? AND is_current = 1 "
            "ORDER BY id DESC LIMIT 1", (company_id, job_id)).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT breakdown_json FROM prospect_scores WHERE company_id = ? AND is_current = 1 ORDER BY id DESC LIMIT 1",
            (company_id,)).fetchone()
    return ScoreBreakdown.model_validate_json(row["breakdown_json"]) if row else None


def current_assessment_jobs(conn: sqlite3.Connection, company_id: int) -> list[int | None]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT job_id FROM qualification_assessments WHERE company_id = ? AND is_current = 1", (company_id,))]


def assessment_history(conn: sqlite3.Connection, company_id: int) -> dict[str, list[dict[str, Any]]]:
    q = conn.execute(
        "SELECT status, target_relevance, assessed_at, method FROM qualification_assessments WHERE company_id = ? ORDER BY id",
        (company_id,),
    ).fetchall()
    s = conn.execute(
        "SELECT total, weights_json, scored_at FROM prospect_scores WHERE company_id = ? ORDER BY id", (company_id,)
    ).fetchall()
    return {
        "qualification": [dict(r) for r in q],
        "scores": [{**dict(r), "weights": loads(r["weights_json"], {})} for r in s],
    }
