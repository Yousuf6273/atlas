"""Crawl jobs, fetched webpages and extracted claims."""

from __future__ import annotations

import sqlite3
from typing import Any

from atlas.db.core import rows_to_dicts
from atlas.models.company import Claim
from atlas.timeutil import now_iso

# ---------------------------------------------------------------- crawl jobs

def ensure_crawl_job(conn: sqlite3.Connection, company_id: int, job_id: int | None, page_budget: int) -> dict[str, Any]:
    conn.execute(
        "INSERT OR IGNORE INTO crawl_jobs(company_id, job_id, status, page_budget) VALUES (?, ?, 'pending', ?)",
        (company_id, job_id, page_budget),
    )
    row = conn.execute(
        "SELECT * FROM crawl_jobs WHERE company_id = ? AND job_id IS ?", (company_id, job_id)
    ).fetchone()
    return dict(row)


def update_crawl_job(conn: sqlite3.Connection, crawl_job_id: int, *, status: str, pages_fetched: int | None = None,
                     error: str | None = None, notes: str | None = None) -> None:
    now = now_iso()
    sets = ["status = ?"]
    params: list[Any] = [status]
    if status == "running":
        sets += ["started_at = ?", "attempts = attempts + 1"]
        params.append(now)
    if status in ("done", "failed", "skipped"):
        sets.append("finished_at = ?")
        params.append(now)
    if pages_fetched is not None:
        sets.append("pages_fetched = ?")
        params.append(pages_fetched)
    if error is not None:
        sets.append("error = ?")
        params.append(error[:2000])
    if notes is not None:
        sets.append("notes = ?")
        params.append(notes[:2000])
    params.append(crawl_job_id)
    conn.execute(f"UPDATE crawl_jobs SET {', '.join(sets)} WHERE id = ?", params)


def crawl_status_counts(conn: sqlite3.Connection, job_id: int) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) n FROM crawl_jobs WHERE job_id = ? GROUP BY status", (job_id,)).fetchall()
    return {r["status"]: r["n"] for r in rows}


# ---------------------------------------------------------------- webpages

def save_webpage(conn: sqlite3.Connection, company_id: int, *, url: str, final_url: str | None, canonical_url: str | None,
                 status_code: int | None, content_type: str | None, page_type: str | None, title: str | None,
                 content_hash: str | None, text_content: str | None, word_count: int | None, lang: str | None,
                 needs_js: bool = False, is_duplicate: bool = False, error: str | None = None,
                 fetched_at: str | None = None) -> int:
    conn.execute(
        "INSERT INTO webpages(company_id, url, final_url, canonical_url, status_code, content_type, page_type, title, "
        "content_hash, text_content, word_count, lang, needs_js, is_duplicate, error, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(company_id, url) DO UPDATE SET final_url = excluded.final_url, canonical_url = excluded.canonical_url, "
        "status_code = excluded.status_code, content_type = excluded.content_type, page_type = excluded.page_type, "
        "title = excluded.title, content_hash = excluded.content_hash, text_content = excluded.text_content, "
        "word_count = excluded.word_count, lang = excluded.lang, needs_js = excluded.needs_js, "
        "is_duplicate = excluded.is_duplicate, error = excluded.error, fetched_at = excluded.fetched_at",
        (company_id, url, final_url, canonical_url, status_code, content_type, page_type, title, content_hash,
         text_content, word_count, lang, int(needs_js), int(is_duplicate), error, fetched_at or now_iso()),
    )
    row = conn.execute("SELECT id FROM webpages WHERE company_id = ? AND url = ?", (company_id, url)).fetchone()
    return int(row["id"])


def company_pages(conn: sqlite3.Connection, company_id: int, *, with_text: bool = True) -> list[dict[str, Any]]:
    cols = "*" if with_text else ("id, company_id, url, final_url, canonical_url, status_code, content_type, page_type, "
                                  "title, content_hash, word_count, lang, needs_js, is_duplicate, error, fetched_at")
    return rows_to_dicts(
        conn.execute(f"SELECT {cols} FROM webpages WHERE company_id = ? ORDER BY id", (company_id,)).fetchall()
    )


# ---------------------------------------------------------------- claims

def retire_claims(conn: sqlite3.Connection, company_id: int, extractor_prefix: str = "rule:",
                  keep: tuple[str, ...] = ()) -> None:
    """Mark existing claims from an extractor family as non-current before re-extraction.

    Retired claims stay in the table (is_current = 0) as history.
    """
    placeholders = ",".join("?" * len(keep)) or "''"
    conn.execute(
        f"UPDATE extracted_claims SET is_current = 0 WHERE company_id = ? AND extractor LIKE ? "
        f"AND extractor NOT IN ({placeholders})",
        (company_id, extractor_prefix + "%", *keep),
    )


def save_claims(conn: sqlite3.Connection, company_id: int, claims: list[Claim]) -> int:
    n = 0
    now = now_iso()
    for c in claims:
        norm = " ".join(c.value.lower().split())[:500]
        conn.execute(
            "INSERT INTO extracted_claims(company_id, claim_type, value, normalized_value, evidence_snippet, source_url, "
            "webpage_id, retrieved_at, extractor, confidence, is_inference, is_current, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?) "
            "ON CONFLICT(company_id, claim_type, normalized_value, source_url) DO UPDATE SET "
            "evidence_snippet = excluded.evidence_snippet, webpage_id = excluded.webpage_id, "
            "retrieved_at = excluded.retrieved_at, extractor = excluded.extractor, confidence = excluded.confidence, "
            "is_inference = excluded.is_inference, is_current = 1",
            (company_id, c.claim_type, c.value[:1000], norm, c.evidence.snippet[:1500], c.evidence.source_url,
             c.evidence.webpage_id, c.evidence.retrieved_at or now, c.extractor, c.confidence, int(c.is_inference), now),
        )
        n += 1
    return n


def company_claims(conn: sqlite3.Connection, company_id: int, claim_type: str | None = None,
                   current_only: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM extracted_claims WHERE company_id = ?"
    params: list[Any] = [company_id]
    if claim_type:
        sql += " AND claim_type = ?"
        params.append(claim_type)
    if current_only:
        sql += " AND is_current = 1"
    sql += " ORDER BY confidence DESC, id"
    return rows_to_dicts(conn.execute(sql, params).fetchall())
