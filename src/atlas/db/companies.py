"""Company persistence: identity, provenance, locations and versioned fields."""

from __future__ import annotations

import sqlite3
from typing import Any

from atlas.db.core import dumps, rows_to_dicts, transaction
from atlas.models.company import CandidateCompany, CandidateLocation
from atlas.timeutil import now_iso

# Default confidence of a value by where it came from. Higher wins; a lower
# confidence value never silently replaces a higher one (see set_field).
SOURCE_CONFIDENCE: dict[str, float] = {
    "user": 1.0,
    "official_site": 0.9,
    "jsonld": 0.9,
    "gleif": 0.8,
    "wikidata": 0.75,
    "osm": 0.7,
    "directory_page": 0.6,
    "manual_csv": 0.85,
    "url_list": 0.95,
    "website_resolution": 0.6,
    "searxng": 0.5,
    "rules": 0.6,
}

VERSIONED_FIELDS = {"name", "website", "domain", "description", "primary_industry", "business_type", "size_band",
                    "size_evidence"}


def create_company(conn: sqlite3.Connection, *, name: str, normalized_name: str, website: str | None = None,
                   domain: str | None = None, website_source: str | None = None,
                   website_confidence: float | None = None, description: str | None = None) -> int:
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO companies(name, normalized_name, website, domain, website_source, website_confidence, description, "
        "identity_status, first_seen_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, normalized_name, website, domain, website_source, website_confidence, description,
         "unverified" if website else "no_website", now, now),
    )
    company_id = int(cur.lastrowid)
    for field, value in (("name", name), ("website", website)):
        if value:
            conn.execute(
                "INSERT INTO field_history(company_id, field, old_value, new_value, old_confidence, new_confidence, source, action, changed_at) "
                "VALUES (?, ?, NULL, ?, NULL, ?, ?, 'set', ?)",
                (company_id, field, value, website_confidence if field == "website" else None, website_source, now),
            )
    return company_id


def get_company(conn: sqlite3.Connection, company_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    return dict(row) if row else None


def resolve_id(conn: sqlite3.Connection, company_id: int) -> int:
    """Follow merged_into_id links to the surviving record."""
    seen = set()
    current = company_id
    while current not in seen:
        seen.add(current)
        row = conn.execute("SELECT merged_into_id FROM companies WHERE id = ?", (current,)).fetchone()
        if not row or row["merged_into_id"] is None:
            return current
        current = int(row["merged_into_id"])
    return current


def set_field(conn: sqlite3.Connection, company_id: int, field: str, value: Any, *, confidence: float,
              source: str) -> bool:
    """Set a company field without losing higher-confidence data.

    Returns True when the stored value changed. Every attempt is recorded in
    ``field_history`` — including values that were *not* applied because the
    existing value had higher confidence.
    """
    if field not in VERSIONED_FIELDS:
        raise ValueError(f"{field} is not a versioned company field")
    row = conn.execute(f"SELECT {field} AS v FROM companies WHERE id = ?", (company_id,)).fetchone()
    if row is None:
        raise KeyError(company_id)
    old = row["v"]
    if old == value:
        return False
    old_conf = _current_confidence(conn, company_id, field)
    now = now_iso()
    if old is not None and old_conf is not None and confidence < old_conf:
        conn.execute(
            "INSERT INTO field_history(company_id, field, old_value, new_value, old_confidence, new_confidence, source, action, changed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'kept_existing', ?)",
            (company_id, field, _s(old), _s(value), old_conf, confidence, source, now),
        )
        return False
    conn.execute(f"UPDATE companies SET {field} = ?, updated_at = ? WHERE id = ?", (value, now, company_id))
    if field == "website":
        conn.execute(
            "UPDATE companies SET website_source = ?, website_confidence = ? WHERE id = ?", (source, confidence, company_id)
        )
    conn.execute(
        "INSERT INTO field_history(company_id, field, old_value, new_value, old_confidence, new_confidence, source, action, changed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (company_id, field, _s(old), _s(value), old_conf, confidence, source, "overwritten" if old is not None else "set", now),
    )
    return True


def _current_confidence(conn: sqlite3.Connection, company_id: int, field: str) -> float | None:
    row = conn.execute(
        "SELECT new_confidence FROM field_history WHERE company_id = ? AND field = ? AND action IN ('set','overwritten') "
        "ORDER BY id DESC LIMIT 1",
        (company_id, field),
    ).fetchone()
    return None if row is None else row["new_confidence"]


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def field_history(conn: sqlite3.Connection, company_id: int) -> list[dict[str, Any]]:
    return rows_to_dicts(
        conn.execute("SELECT * FROM field_history WHERE company_id = ? ORDER BY id", (company_id,)).fetchall()
    )


def set_identity(conn: sqlite3.Connection, company_id: int, status: str, notes: str | None) -> None:
    conn.execute(
        "UPDATE companies SET identity_status = ?, identity_notes = ?, updated_at = ? WHERE id = ?",
        (status, notes, now_iso(), company_id),
    )


def mark_researched(conn: sqlite3.Connection, company_id: int, *, qualification_status: str | None = None,
                    internship_category: str | None = None) -> None:
    conn.execute(
        "UPDATE companies SET last_researched_at = ?, updated_at = ?, "
        "qualification_status = COALESCE(?, qualification_status), internship_category = COALESCE(?, internship_category) "
        "WHERE id = ?",
        (now_iso(), now_iso(), qualification_status, internship_category, company_id),
    )


# ---------------------------------------------------------------- provenance

def find_by_source(conn: sqlite3.Connection, provider: str, external_id: str) -> int | None:
    row = conn.execute(
        "SELECT company_id FROM company_sources WHERE provider = ? AND external_id = ?", (provider, external_id)
    ).fetchone()
    return resolve_id(conn, int(row["company_id"])) if row else None


def add_source(conn: sqlite3.Connection, company_id: int, cand: CandidateCompany, job_id: int | None) -> None:
    raw = cand.model_dump(mode="json")
    conn.execute(
        "INSERT INTO company_sources(company_id, provider, external_id, raw_name, source_url, raw_json, job_id, retrieved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(provider, external_id) DO UPDATE SET raw_name = excluded.raw_name, "
        "source_url = excluded.source_url, raw_json = excluded.raw_json, retrieved_at = excluded.retrieved_at, "
        "job_id = COALESCE(excluded.job_id, company_sources.job_id)",
        (company_id, cand.provider, cand.external_id, cand.name, cand.source_url, dumps(raw), job_id,
         cand.retrieved_at or now_iso()),
    )


def company_sources(conn: sqlite3.Connection, company_id: int) -> list[dict[str, Any]]:
    return rows_to_dicts(
        conn.execute("SELECT * FROM company_sources WHERE company_id = ? ORDER BY id", (company_id,)).fetchall()
    )


def add_location(conn: sqlite3.Connection, company_id: int, loc: CandidateLocation, *, provider: str,
                 source_url: str | None) -> None:
    if not any([loc.address, loc.city, loc.latitude is not None]):
        return
    conn.execute(
        "INSERT OR IGNORE INTO company_locations(company_id, address, city, emirate, country_code, latitude, longitude, "
        "location_type, source_provider, source_url, retrieved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (company_id, loc.address, loc.city, loc.emirate, loc.country_code, loc.latitude, loc.longitude,
         loc.location_type.value, provider, source_url, now_iso()),
    )


def company_locations(conn: sqlite3.Connection, company_id: int) -> list[dict[str, Any]]:
    return rows_to_dicts(
        conn.execute("SELECT * FROM company_locations WHERE company_id = ? ORDER BY id", (company_id,)).fetchall()
    )


def link_job(conn: sqlite3.Connection, job_id: int, company_id: int, prefilter_score: float | None = None) -> None:
    conn.execute(
        "INSERT INTO job_companies(job_id, company_id, added_at, prefilter_score) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(job_id, company_id) DO UPDATE SET prefilter_score = MAX(COALESCE(job_companies.prefilter_score, 0), "
        "COALESCE(excluded.prefilter_score, 0))",
        (job_id, company_id, now_iso(), prefilter_score),
    )


def set_selected_for_research(conn: sqlite3.Connection, job_id: int, company_ids: list[int]) -> None:
    conn.executemany(
        "UPDATE job_companies SET selected_for_research = 1 WHERE job_id = ? AND company_id = ?",
        [(job_id, cid) for cid in company_ids],
    )


def job_company_ids(conn: sqlite3.Connection, job_id: int, *, selected_only: bool = False) -> list[int]:
    sql = (
        "SELECT jc.company_id FROM job_companies jc JOIN companies c ON c.id = jc.company_id "
        "WHERE jc.job_id = ? AND c.merged_into_id IS NULL"
    )
    if selected_only:
        sql += " AND jc.selected_for_research = 1"
    sql += " ORDER BY COALESCE(jc.prefilter_score, 0) DESC, jc.company_id"
    return [int(r[0]) for r in conn.execute(sql, (job_id,)).fetchall()]


# ---------------------------------------------------------------- matching support

def match_pool(conn: sqlite3.Connection, *, domain: str | None, phones: list[str], name_tokens: list[str],
               limit: int = 200) -> list[dict[str, Any]]:
    """Return live companies that share a blocking key with the candidate."""
    ids: set[int] = set()
    if domain:
        ids |= {r[0] for r in conn.execute(
            "SELECT id FROM companies WHERE domain = ? AND merged_into_id IS NULL", (domain,))}
    for ph in phones:
        ids |= {r[0] for r in conn.execute(
            "SELECT c.id FROM contacts ct JOIN companies c ON c.id = ct.company_id "
            "WHERE ct.channel = 'phone' AND ct.normalized_value = ? AND c.merged_into_id IS NULL", (ph,))}
    for tok in name_tokens[:3]:
        if len(tok) < 3:
            continue
        ids |= {r[0] for r in conn.execute(
            "SELECT id FROM companies WHERE merged_into_id IS NULL AND (' ' || normalized_name || ' ') LIKE ? LIMIT ?",
            (f"% {tok} %", limit))}
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = rows_to_dicts(conn.execute(f"SELECT * FROM companies WHERE id IN ({placeholders})", list(ids)).fetchall())
    for r in rows:
        r["phones"] = [x[0] for x in conn.execute(
            "SELECT normalized_value FROM contacts WHERE company_id = ? AND channel = 'phone'", (r["id"],))]
        r["coords"] = [(x[0], x[1]) for x in conn.execute(
            "SELECT latitude, longitude FROM company_locations WHERE company_id = ? AND latitude IS NOT NULL", (r["id"],))]
    return rows


def merge_companies(conn: sqlite3.Connection, keep_id: int, drop_id: int, *, reason: str) -> None:
    """Merge ``drop_id`` into ``keep_id`` preserving all provenance."""
    if keep_id == drop_id:
        return
    with transaction(conn):
        keep = get_company(conn, keep_id)
        drop = get_company(conn, drop_id)
        if keep is None or drop is None:
            raise KeyError("company not found")
        for table in ("company_sources", "company_locations", "webpages", "extracted_claims", "contacts",
                      "outreach_drafts", "outreach_history", "do_not_contact", "crawl_jobs", "evaluation_reviews"):
            conn.execute(f"UPDATE OR IGNORE {table} SET company_id = ? WHERE company_id = ?", (keep_id, drop_id))
            conn.execute(f"DELETE FROM {table} WHERE company_id = ?", (drop_id,))
        conn.execute(
            "INSERT OR IGNORE INTO job_companies(job_id, company_id, added_at, prefilter_score, selected_for_research) "
            "SELECT job_id, ?, added_at, prefilter_score, selected_for_research FROM job_companies WHERE company_id = ?",
            (keep_id, drop_id),
        )
        for field in ("website", "domain", "description"):
            if keep.get(field) is None and drop.get(field) is not None:
                conn.execute(f"UPDATE companies SET {field} = ? WHERE id = ?", (drop[field], keep_id))
        conn.execute(
            "UPDATE companies SET merged_into_id = ?, updated_at = ? WHERE id = ?", (keep_id, now_iso(), drop_id)
        )
        conn.execute(
            "INSERT INTO field_history(company_id, field, old_value, new_value, source, action, changed_at) "
            "VALUES (?, 'merged_from', ?, ?, ?, 'set', ?)",
            (keep_id, str(drop_id), drop["name"], reason, now_iso()),
        )
        conn.execute(
            "UPDATE review_queue SET status = 'resolved', resolution = 'merged', resolved_at = ? "
            "WHERE status = 'open' AND ((company_id = ? AND other_company_id = ?) OR (company_id = ? AND other_company_id = ?))",
            (now_iso(), keep_id, drop_id, drop_id, keep_id),
        )
