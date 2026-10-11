"""Contact channels, their evidence and verification results."""

from __future__ import annotations

import sqlite3
from typing import Any

from atlas.db.core import rows_to_dicts
from atlas.models.contact import ContactCandidate, EmailVerification
from atlas.timeutil import now_iso


def normalize_contact_value(channel: str, value: str) -> str:
    v = value.strip()
    if channel == "email":
        return v.lower()
    if channel in ("contact_form", "careers_page", "linkedin_company_page", "website"):
        return v.rstrip("/").lower()
    return v


def upsert_contact(conn: sqlite3.Connection, company_id: int, c: ContactCandidate) -> int:
    now = now_iso()
    norm = normalize_contact_value(c.channel.value, c.value)
    existing = conn.execute(
        "SELECT * FROM contacts WHERE company_id = ? AND channel = ? AND normalized_value = ?",
        (company_id, c.channel.value, norm),
    ).fetchone()
    if existing is None:
        cur = conn.execute(
            "INSERT INTO contacts(company_id, channel, value, normalized_value, name, role, role_category, is_generic, "
            "source_type, source_url, retrieved_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (company_id, c.channel.value, c.value.strip(), norm, c.name, c.role, c.role_category.value, int(c.is_generic),
             c.source_type, c.source_url, c.retrieved_at or now, now, now),
        )
        contact_id = int(cur.lastrowid)
    else:
        contact_id = int(existing["id"])
        # Prefer official-site provenance and keep any name/role already known.
        better_source = existing["source_type"] != "official_site" and c.source_type == "official_site"
        conn.execute(
            "UPDATE contacts SET name = COALESCE(name, ?), role = COALESCE(role, ?), "
            "role_category = CASE WHEN role_category = 'general' THEN ? ELSE role_category END, "
            "source_type = CASE WHEN ? THEN ? ELSE source_type END, "
            "source_url = CASE WHEN ? THEN ? ELSE COALESCE(source_url, ?) END, "
            "is_current = 1, updated_at = ? WHERE id = ?",
            (c.name, c.role, c.role_category.value, better_source, c.source_type, better_source, c.source_url,
             c.source_url, now, contact_id),
        )
    if c.source_url:
        conn.execute(
            "INSERT INTO contact_evidence(contact_id, source_url, snippet, retrieved_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(contact_id, source_url) DO UPDATE SET snippet = COALESCE(excluded.snippet, contact_evidence.snippet), "
            "retrieved_at = excluded.retrieved_at",
            (contact_id, c.source_url, (c.snippet or "")[:600] or None, c.retrieved_at or now),
        )
    return contact_id


def save_verification(conn: sqlite3.Connection, contact_id: int, v: EmailVerification) -> None:
    now = now_iso()
    conn.execute("DELETE FROM verification_results WHERE contact_id = ?", (contact_id,))
    conn.executemany(
        "INSERT INTO verification_results(contact_id, check_name, passed, detail, checked_at) VALUES (?, ?, ?, ?, ?)",
        [(contact_id, ch.check, None if ch.passed is None else int(ch.passed), ch.detail, now) for ch in v.checks],
    )
    conn.execute(
        "UPDATE contacts SET verification_status = ?, updated_at = ? WHERE id = ?", (v.status.value, now, contact_id)
    )


def set_quality(conn: sqlite3.Connection, contact_id: int, score: float, status: str | None = None) -> None:
    if status:
        conn.execute("UPDATE contacts SET quality_score = ?, verification_status = ? WHERE id = ?",
                     (score, status, contact_id))
    else:
        conn.execute("UPDATE contacts SET quality_score = ? WHERE id = ?", (score, contact_id))


def company_contacts(conn: sqlite3.Connection, company_id: int) -> list[dict[str, Any]]:
    rows = rows_to_dicts(conn.execute(
        "SELECT * FROM contacts WHERE company_id = ? AND is_current = 1 ORDER BY quality_score DESC, id", (company_id,)
    ).fetchall())
    for r in rows:
        r["evidence"] = rows_to_dicts(conn.execute(
            "SELECT source_url, snippet, retrieved_at FROM contact_evidence WHERE contact_id = ? ORDER BY id", (r["id"],)
        ).fetchall())
        r["checks"] = rows_to_dicts(conn.execute(
            "SELECT check_name, passed, detail, checked_at FROM verification_results WHERE contact_id = ? ORDER BY id",
            (r["id"],)
        ).fetchall())
    return rows


def get_contact(conn: sqlite3.Connection, contact_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    return dict(row) if row else None
