"""Mini-CRM rules: status transitions, duplicate-outreach prevention, opt-outs.

ATLAS never sends messages. A message only counts as sent when the student
records it here (status 'contacted' with explicit confirmation).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from atlas.db import companies, outreach
from atlas.models.enums import SENT_STATUSES, OutreachStatus


class OutreachBlocked(Exception):
    """Raised when recording an action would violate do-not-contact or duplicate rules."""


@dataclass
class StatusCheck:
    allowed: bool
    reason: str | None = None
    requires_confirmation: bool = False


def current_status(conn: sqlite3.Connection, company_id: int) -> OutreachStatus:
    if outreach.is_dnc(conn, company_id=company_id):
        return OutreachStatus.DO_NOT_CONTACT
    latest = outreach.latest_status(conn, company_id)
    if latest:
        return OutreachStatus(latest["status"])
    if outreach.company_drafts(conn, company_id):
        return OutreachStatus.DRAFT_PREPARED
    return OutreachStatus.NOT_CONTACTED


def check_transition(conn: sqlite3.Connection, company_id: int, new_status: OutreachStatus, *,
                     contact_value: str | None = None) -> StatusCheck:
    company = companies.get_company(conn, company_id)
    if company is None:
        return StatusCheck(False, "company not found")
    if new_status == OutreachStatus.DO_NOT_CONTACT:
        return StatusCheck(True)
    reason = outreach.is_dnc(conn, company_id=company_id, domain=company.get("domain"), email=contact_value)
    if reason and new_status in (OutreachStatus.CONTACTED, OutreachStatus.AWAITING_RESPONSE,
                                 OutreachStatus.DRAFT_PREPARED):
        return StatusCheck(False, f"on the do-not-contact list: {reason}")
    cur = current_status(conn, company_id)
    if new_status == OutreachStatus.CONTACTED:
        if cur in SENT_STATUSES:
            return StatusCheck(True, f"already recorded as '{cur.value}' — record this as a follow-up?",
                               requires_confirmation=True)
        if contact_value:
            used = outreach.sent_contact_values(conn).get(contact_value.lower())
            if used is not None and used != company_id:
                return StatusCheck(True, "this address was already contacted for another company record "
                                         "(possible duplicate company)", requires_confirmation=True)
    return StatusCheck(True)


def record(conn: sqlite3.Connection, company_id: int, new_status: OutreachStatus, *, confirmed: bool = False,
           contact_id: int | None = None, channel: str | None = None, contact_value: str | None = None,
           draft_id: int | None = None, message_snapshot: str | None = None, event_date: str | None = None,
           follow_up_date: str | None = None, notes: str | None = None, outcome: str | None = None) -> int:
    chk = check_transition(conn, company_id, new_status, contact_value=contact_value)
    if not chk.allowed:
        raise OutreachBlocked(chk.reason or "not allowed")
    if chk.requires_confirmation and not confirmed:
        raise OutreachBlocked(chk.reason or "confirmation required")
    if new_status == OutreachStatus.CONTACTED and not confirmed:
        raise OutreachBlocked("Confirm that you actually sent the message before recording it as contacted.")
    if new_status == OutreachStatus.DO_NOT_CONTACT:
        company = companies.get_company(conn, company_id)
        outreach.add_dnc(conn, company_id=company_id, domain=company.get("domain") if company else None,
                         reason=notes or "marked do-not-contact")
    return outreach.add_history(conn, company_id, status=new_status.value, contact_id=contact_id, channel=channel,
                                contact_value=contact_value, draft_id=draft_id, message_snapshot=message_snapshot,
                                event_date=event_date, follow_up_date=follow_up_date, notes=notes, outcome=outcome)
