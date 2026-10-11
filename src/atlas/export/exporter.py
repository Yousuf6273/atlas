"""CSV / XLSX export with spreadsheet-formula-injection protection.

Untrusted text (company names, scraped snippets, URLs) beginning with ``=``,
``+``, ``-``, ``@``, tab or carriage return is prefixed with an apostrophe so
spreadsheet applications show it as text instead of evaluating it (OWASP
"CSV injection" guidance). Numeric columns are exported as numbers.
"""

from __future__ import annotations

import io
import sqlite3
from typing import Any

import pandas as pd

from atlas.db.queries import prospect_rows
from atlas.models.enums import (
    BUSINESS_TYPE_LABELS,
    EMAIL_STATUS_LABELS,
    INDUSTRY_LABELS,
    INTERNSHIP_LABELS,
    OUTREACH_LABELS,
    ROLE_LABELS,
    SIZE_LABELS,
)

DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n", "｜", "＝", "＋", "－", "＠")

EXPORT_COLUMNS = [
    ("Company", "name"), ("Website", "website"), ("Industry", "industry_label"), ("Business type", "business_type_label"),
    ("Location", "location"), ("Approximate size", "size_label"), ("Size evidence", "size_evidence"),
    ("Qualification", "qualification_status"), ("Internship suitability", "internship_label"),
    ("Proposed tasks", "tasks_text"), ("Best contact role", "contact_role_label"),
    ("Public email / official contact URL", "best_contact"), ("Contact channel", "best_channel"),
    ("Contact verification", "verification_label"), ("Contact source", "contact_source_url"),
    ("Fit score", "total_score"), ("Suitability", "suitability"), ("Evidence confidence", "evidence_confidence"),
    ("Contact quality", "contact_score"), ("Outreach readiness", "readiness_label"),
    ("Evidence links", "evidence_text"), ("Discovery sources", "sources_text"),
    ("Research timestamp (UTC)", "last_researched_at"), ("Outreach status", "outreach_label"),
    ("Follow-up date", "follow_up_date"), ("ATLAS id", "company_id"),
]
NUMERIC = {"total_score", "suitability", "evidence_confidence", "contact_score", "company_id"}


def safe_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return value
    s = str(value)
    if s.startswith(DANGEROUS_PREFIXES):
        return "'" + s
    return s


def _row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        **r,
        "industry_label": INDUSTRY_LABELS.get(r["primary_industry"] or "", r["primary_industry"] or ""),
        "business_type_label": BUSINESS_TYPE_LABELS.get(r["business_type"] or "", r["business_type"] or ""),
        "size_label": SIZE_LABELS.get(r["size_band"] or "unknown", "Unknown"),
        "internship_label": INTERNSHIP_LABELS.get(r["internship_category"] or "", "Not assessed"),
        "tasks_text": "; ".join(r["top_tasks"]),
        "contact_role_label": (f"{r['best_contact_name']} — {r['best_contact_role']}" if r.get("best_contact_name")
                               else ROLE_LABELS.get(r.get("best_contact_role_category") or "", "")),
        "verification_label": EMAIL_STATUS_LABELS.get(r["contact_verification"] or "", r["contact_verification"] or ""),
        "evidence_text": " | ".join(r["evidence_links"]),
        "sources_text": ", ".join(r["sources"]),
        "outreach_label": OUTREACH_LABELS.get(r["outreach_status"], r["outreach_status"]),
    }


def build_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    data = []
    for r in rows:
        rr = _row(r)
        data.append({label: (rr.get(key) if key in NUMERIC else safe_cell(rr.get(key)))
                     for label, key in EXPORT_COLUMNS})
    return pd.DataFrame(data, columns=[c for c, _ in EXPORT_COLUMNS])


def export_rows(conn: sqlite3.Connection, *, job_id: int | None = None, company_ids: list[int] | None = None
                ) -> pd.DataFrame:
    return build_dataframe(prospect_rows(conn, job_id=job_id, company_ids=company_ids))


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")  # BOM so Excel opens UTF-8 correctly


def to_xlsx_bytes(df: pd.DataFrame, sheet: str = "Prospects") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name=sheet)
        ws = xw.sheets[sheet]
        for col in ws.columns:
            width = min(60, max(10, max(len(str(c.value or "")) for c in col[:200]) + 2))
            ws.column_dimensions[col[0].column_letter].width = width
            for cell in col[1:]:
                # Store strings as literal text, never as formulas.
                if isinstance(cell.value, str):
                    cell.data_type = "s"
        ws.freeze_panes = "B2"
    return buf.getvalue()
