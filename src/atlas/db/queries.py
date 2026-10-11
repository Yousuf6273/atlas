"""Read models for the UI, exports and analytics."""

from __future__ import annotations

import sqlite3
from typing import Any

from atlas.db.core import loads
from atlas.geo.areas import AREAS, EMIRATE_NAMES
from atlas.geo.geometry import emirate_of

USABLE_CHANNELS = ("email", "contact_form", "careers_page")


def _location_summary(conn: sqlite3.Connection, cid: int) -> str:
    rows = conn.execute("SELECT city, latitude, longitude, address, source_provider FROM company_locations "
                        "WHERE company_id = ? ORDER BY id", (cid,)).fetchall()
    for r in rows:
        if r["latitude"] is not None:
            em = emirate_of(r["latitude"], r["longitude"])
            if em:
                return f"{EMIRATE_NAMES[em]} (map coordinates)"
    for r in rows:
        if r["city"]:
            return f"{r['city'].title()} ({r['source_provider']})"
    m = conn.execute("SELECT value FROM extracted_claims WHERE company_id = ? AND claim_type = 'location_mention' "
                     "AND is_current = 1 LIMIT 1", (cid,)).fetchone()
    if m and m["value"] in AREAS:
        return f"{AREAS[m['value']].label} (website)"
    return "Unknown"


def prospect_rows(conn: sqlite3.Connection, *, job_id: int | None = None, company_ids: list[int] | None = None,
                  include_unassessed: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT c.* FROM companies c WHERE c.merged_into_id IS NULL"
    params: list[Any] = []
    if job_id is not None:
        sql += " AND c.id IN (SELECT company_id FROM job_companies WHERE job_id = ?)"
        params.append(job_id)
    if company_ids is not None:
        if not company_ids:
            return []
        sql += f" AND c.id IN ({','.join('?' * len(company_ids))})"
        params += company_ids
    if not include_unassessed:
        sql += " AND c.qualification_status IS NOT NULL"
    companies = [dict(r) for r in conn.execute(sql, params).fetchall()]
    latest = {int(r["company_id"]): dict(r) for r in conn.execute(
        "SELECT h.* FROM outreach_history h JOIN (SELECT company_id, MAX(id) mid FROM outreach_history GROUP BY company_id) m "
        "ON m.mid = h.id").fetchall()}
    drafts = {int(r[0]) for r in conn.execute("SELECT DISTINCT company_id FROM outreach_drafts")}
    dnc_ids = {int(r[0]) for r in conn.execute("SELECT company_id FROM do_not_contact WHERE company_id IS NOT NULL")}
    dnc_domains = {r[0] for r in conn.execute("SELECT domain FROM do_not_contact WHERE domain IS NOT NULL")}
    out = []
    for c in companies:
        cid = c["id"]
        # Scores and qualification are per search: show this job's verdict when a job is selected.
        if job_id is not None:
            s = conn.execute("SELECT * FROM prospect_scores WHERE company_id = ? AND job_id = ? AND is_current = 1 "
                             "ORDER BY id DESC LIMIT 1", (cid, job_id)).fetchone()
            qrow = conn.execute("SELECT status FROM qualification_assessments WHERE company_id = ? AND job_id = ? "
                                "AND is_current = 1 ORDER BY id DESC LIMIT 1", (cid, job_id)).fetchone()
            qual_status = qrow["status"] if qrow else None  # not assessed for this search
        else:
            s = conn.execute("SELECT * FROM prospect_scores WHERE company_id = ? AND is_current = 1 ORDER BY id DESC "
                             "LIMIT 1", (cid,)).fetchone()
            qual_status = c["qualification_status"]
        best = conn.execute(
            f"SELECT * FROM contacts WHERE company_id = ? AND is_current = 1 AND channel IN ({','.join('?' * len(USABLE_CHANNELS))}) "
            "AND quality_score > 0 ORDER BY quality_score DESC, id LIMIT 1", (cid, *USABLE_CHANNELS)).fetchone()
        tasks = [r[0] for r in conn.execute(
            "SELECT title FROM task_opportunities WHERE company_id = ? AND is_current = 1 ORDER BY id LIMIT 3", (cid,))]
        n_tasks = conn.execute("SELECT COUNT(*) FROM task_opportunities WHERE company_id = ? AND is_current = 1",
                               (cid,)).fetchone()[0]
        ev = [r[0] for r in conn.execute(
            "SELECT DISTINCT source_url FROM extracted_claims WHERE company_id = ? AND is_current = 1 AND "
            "claim_type IN ('activity','offering','internship_signal','eligibility_school_ok') "
            "AND source_url LIKE 'http%' LIMIT 3", (cid,))]
        sources = sorted({r[0] for r in conn.execute("SELECT provider FROM company_sources WHERE company_id = ?", (cid,))})
        h = latest.get(cid)
        if cid in dnc_ids or (c.get("domain") and c["domain"] in dnc_domains):
            outreach_status = "do_not_contact"
        elif h:
            outreach_status = h["status"]
        elif cid in drafts:
            outreach_status = "draft_prepared"
        else:
            outreach_status = "not_contacted"
        out.append({
            "company_id": cid, "name": c["name"], "website": c["website"], "domain": c["domain"],
            "primary_industry": c["primary_industry"], "business_type": c["business_type"],
            "size_band": c["size_band"], "size_evidence": c["size_evidence"],
            "qualification_status": qual_status, "internship_category": c["internship_category"],
            "identity_status": c["identity_status"], "location": _location_summary(conn, cid),
            "total_score": s["total"] if s else None, "industry_score": s["industry"] if s else None,
            "tasks_score": s["tasks"] if s else None, "internship_score": s["internship"] if s else None,
            "contact_score": s["contact"] if s else None, "geography_score": s["geography"] if s else None,
            "activity_score": s["activity"] if s else None, "suitability": s["suitability"] if s else None,
            "evidence_confidence": s["evidence_confidence"] if s else None,
            "outreach_readiness": s["outreach_readiness"] if s else None,
            "readiness_label": s["readiness_label"] if s else "Not assessed",
            "best_channel": best["channel"] if best else None, "best_contact": best["value"] if best else None,
            "best_contact_name": best["name"] if best else None, "best_contact_role": best["role"] if best else None,
            "best_contact_role_category": best["role_category"] if best else None,
            "contact_verification": best["verification_status"] if best else None,
            "contact_source_url": best["source_url"] if best else None,
            "has_usable_contact": best is not None,
            "n_tasks": n_tasks, "top_tasks": tasks, "evidence_links": ev, "sources": sources,
            "outreach_status": outreach_status,
            "last_contacted": h["event_date"] if h and h["status"] in ("contacted", "awaiting_response") else None,
            "follow_up_date": h["follow_up_date"] if h else None,
            "last_researched_at": c["last_researched_at"], "first_seen_at": c["first_seen_at"],
        })
    out.sort(key=lambda r: (r["total_score"] is None, -(r["total_score"] or 0)))
    return out


def overview_counts(conn: sqlite3.Connection) -> dict[str, int]:
    one = lambda sql, *p: int(conn.execute(sql, p).fetchone()[0])  # noqa: E731
    live = "merged_into_id IS NULL"
    return {
        "discovered": one(f"SELECT COUNT(*) FROM companies WHERE {live}"),
        "researched": one(f"SELECT COUNT(*) FROM companies WHERE {live} AND last_researched_at IS NOT NULL"),
        "qualified": one(f"SELECT COUNT(*) FROM companies WHERE {live} AND qualification_status = 'qualified'"),
        "needs_review": one(f"SELECT COUNT(*) FROM companies WHERE {live} AND qualification_status = 'needs_review'"),
        "rejected": one(f"SELECT COUNT(*) FROM companies WHERE {live} AND qualification_status = 'rejected'"),
        "usable_contact": one(
            f"SELECT COUNT(DISTINCT c.id) FROM companies c JOIN contacts ct ON ct.company_id = c.id WHERE c.{live} "
            "AND ct.is_current = 1 AND ct.quality_score > 0 AND ct.channel IN ('email','contact_form','careers_page')"),
        "drafts": one("SELECT COUNT(DISTINCT company_id) FROM outreach_drafts"),
        "approved_drafts": one("SELECT COUNT(*) FROM outreach_drafts WHERE approved = 1"),
        "contacted": one("SELECT COUNT(DISTINCT company_id) FROM outreach_history WHERE status IN "
                         "('contacted','awaiting_response','replied','interested','meeting_arranged','rejected','placement_offered')"),
        "replied": one("SELECT COUNT(DISTINCT company_id) FROM outreach_history WHERE status IN "
                       "('replied','interested','meeting_arranged','rejected','placement_offered')"),
        "opportunities": one("SELECT COUNT(DISTINCT company_id) FROM outreach_history WHERE status IN "
                             "('interested','meeting_arranged','placement_offered')"),
        "confirmed_programmes": one(f"SELECT COUNT(*) FROM companies WHERE {live} AND internship_category = 'confirmed_opportunity'"),
        "review_open": one("SELECT COUNT(*) FROM review_queue r JOIN companies c ON c.id = r.company_id "
                           f"WHERE r.status = 'open' AND c.{live}"),
    }


def company_job_ids(conn: sqlite3.Connection, company_id: int) -> list[int]:
    return [int(r[0]) for r in conn.execute("SELECT job_id FROM job_companies WHERE company_id = ? ORDER BY job_id DESC",
                                            (company_id,))]


def follow_ups_due(conn: sqlite3.Connection, today: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT h.*, c.name FROM outreach_history h JOIN (SELECT company_id, MAX(id) mid FROM outreach_history GROUP BY company_id) m "
        "ON m.mid = h.id JOIN companies c ON c.id = h.company_id WHERE h.follow_up_date IS NOT NULL AND h.follow_up_date <= ? "
        "AND h.status IN ('contacted','awaiting_response') ORDER BY h.follow_up_date", (today,)).fetchall()
    return [dict(r) for r in rows]


def source_coverage(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT cs.provider, COUNT(DISTINCT cs.company_id) companies, "
        "SUM(CASE WHEN c.qualification_status = 'qualified' THEN 1 ELSE 0 END) qualified, "
        "SUM(CASE WHEN c.website IS NOT NULL THEN 1 ELSE 0 END) with_website "
        "FROM company_sources cs JOIN companies c ON c.id = cs.company_id WHERE c.merged_into_id IS NULL "
        "GROUP BY cs.provider ORDER BY companies DESC").fetchall()
    return [dict(r) for r in rows]


def claim_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT claim_type, COUNT(*) n, SUM(is_inference) inf FROM extracted_claims WHERE is_current = 1 "
                        "GROUP BY claim_type ORDER BY n DESC").fetchall()
    return {r["claim_type"]: {"count": r["n"], "inferences": r["inf"] or 0} for r in rows}


def provider_run_summary(conn: sqlite3.Connection, job_id: int) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT pr.*, dp.display_name FROM provider_runs pr JOIN discovery_providers dp ON "
                        "dp.name = pr.provider WHERE pr.job_id = ? ORDER BY pr.id", (job_id,)).fetchall()
    return [dict(r) for r in rows]


def job_summary(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM search_jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return {}
    d = dict(row)
    d["stats"] = loads(d.pop("stats_json"), {})
    d["criteria"] = loads(d.pop("criteria_json"), {})
    return d
