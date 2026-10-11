"""Outreach drafts, CRM history and the do-not-contact list."""

from __future__ import annotations

import sqlite3
from typing import Any

from atlas.db.core import dumps, loads, rows_to_dicts
from atlas.timeutil import now_iso

# ---------------------------------------------------------------- drafts

def save_draft(conn: sqlite3.Connection, company_id: int, *, kind: str, subject: str | None, body: str,
               facts: list[dict[str, Any]], method: str, contact_id: int | None = None) -> int:
    """Replace the un-approved draft of this kind; approved drafts are never overwritten."""
    now = now_iso()
    existing = conn.execute(
        "SELECT id, approved FROM outreach_drafts WHERE company_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
        (company_id, kind),
    ).fetchone()
    if existing and not existing["approved"]:
        conn.execute(
            "UPDATE outreach_drafts SET subject = ?, body = ?, facts_json = ?, method = ?, contact_id = ?, "
            "generated_at = ?, updated_at = ? WHERE id = ?",
            (subject, body, dumps(facts), method, contact_id, now, now, existing["id"]),
        )
        return int(existing["id"])
    cur = conn.execute(
        "INSERT INTO outreach_drafts(company_id, contact_id, kind, subject, body, facts_json, method, generated_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (company_id, contact_id, kind, subject, body, dumps(facts), method, now, now),
    )
    return int(cur.lastrowid)


def update_draft_text(conn: sqlite3.Connection, draft_id: int, *, subject: str | None, edited_body: str) -> None:
    conn.execute(
        "UPDATE outreach_drafts SET subject = ?, edited_body = ?, updated_at = ? WHERE id = ?",
        (subject, edited_body, now_iso(), draft_id),
    )


def set_draft_approved(conn: sqlite3.Connection, draft_id: int, approved: bool) -> None:
    conn.execute(
        "UPDATE outreach_drafts SET approved = ?, approved_at = ?, updated_at = ? WHERE id = ?",
        (int(approved), now_iso() if approved else None, now_iso(), draft_id),
    )


def company_drafts(conn: sqlite3.Connection, company_id: int) -> list[dict[str, Any]]:
    rows = rows_to_dicts(conn.execute(
        "SELECT * FROM outreach_drafts WHERE company_id = ? ORDER BY id", (company_id,)
    ).fetchall())
    for r in rows:
        r["facts"] = loads(r.pop("facts_json"), [])
        r["final_body"] = r["edited_body"] or r["body"]
    return rows


def get_draft(conn: sqlite3.Connection, draft_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM outreach_drafts WHERE id = ?", (draft_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["facts"] = loads(d.pop("facts_json"), [])
    d["final_body"] = d["edited_body"] or d["body"]
    return d


# ---------------------------------------------------------------- history

def add_history(conn: sqlite3.Connection, company_id: int, *, status: str, event_date: str | None = None,
                contact_id: int | None = None, channel: str | None = None, contact_value: str | None = None,
                draft_id: int | None = None, message_snapshot: str | None = None, follow_up_date: str | None = None,
                notes: str | None = None, outcome: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO outreach_history(company_id, contact_id, status, channel, contact_value, draft_id, message_snapshot, "
        "event_date, follow_up_date, notes, outcome, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (company_id, contact_id, status, channel, contact_value, draft_id, message_snapshot,
         event_date or now_iso()[:10], follow_up_date, notes, outcome, now_iso()),
    )
    return int(cur.lastrowid)


def company_history(conn: sqlite3.Connection, company_id: int) -> list[dict[str, Any]]:
    return rows_to_dicts(conn.execute(
        "SELECT * FROM outreach_history WHERE company_id = ? ORDER BY id", (company_id,)
    ).fetchall())


def latest_status(conn: sqlite3.Connection, company_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM outreach_history WHERE company_id = ? ORDER BY id DESC LIMIT 1", (company_id,)
    ).fetchone()
    return dict(row) if row else None


def all_latest_statuses(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        "SELECT h.* FROM outreach_history h JOIN (SELECT company_id, MAX(id) mid FROM outreach_history GROUP BY company_id) m "
        "ON m.mid = h.id"
    ).fetchall()
    return {int(r["company_id"]): dict(r) for r in rows}


def sent_contact_values(conn: sqlite3.Connection) -> dict[str, int]:
    """Contact values (emails, form URLs) already used in a recorded send → company_id."""
    rows = conn.execute(
        "SELECT contact_value, company_id FROM outreach_history WHERE contact_value IS NOT NULL AND "
        "status IN ('contacted','awaiting_response')"
    ).fetchall()
    return {r["contact_value"].lower(): int(r["company_id"]) for r in rows}


# ---------------------------------------------------------------- do-not-contact

def add_dnc(conn: sqlite3.Connection, *, company_id: int | None = None, domain: str | None = None,
            email: str | None = None, reason: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO do_not_contact(company_id, domain, email, reason, added_at) VALUES (?, ?, ?, ?, ?)",
        (company_id, domain.lower() if domain else None, email.lower() if email else None, reason, now_iso()),
    )
    return int(cur.lastrowid)


def remove_dnc(conn: sqlite3.Connection, dnc_id: int) -> None:
    conn.execute("DELETE FROM do_not_contact WHERE id = ?", (dnc_id,))


def list_dnc(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows_to_dicts(conn.execute(
        "SELECT d.*, c.name AS company_name FROM do_not_contact d LEFT JOIN companies c ON c.id = d.company_id ORDER BY d.id DESC"
    ).fetchall())


def is_dnc(conn: sqlite3.Connection, *, company_id: int | None = None, domain: str | None = None,
           email: str | None = None) -> str | None:
    """Return the reason if any rule blocks contact, else None."""
    checks = []
    if company_id is not None:
        checks.append(("company_id = ?", company_id))
    if domain:
        checks.append(("domain = ?", domain.lower()))
    if email:
        checks.append(("email = ?", email.lower()))
        if "@" in email:
            checks.append(("domain = ?", email.split("@", 1)[1].lower()))
    for clause, val in checks:
        row = conn.execute(f"SELECT reason FROM do_not_contact WHERE {clause} LIMIT 1", (val,)).fetchone()
        if row:
            return row["reason"] or "on do-not-contact list"
    return None
