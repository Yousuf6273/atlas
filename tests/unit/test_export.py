"""Export safety: formula-injection escaping, numeric columns, XLSX round-trip."""

from __future__ import annotations

import io

import openpyxl

from atlas.export.exporter import (
    EXPORT_COLUMNS,
    build_dataframe,
    safe_cell,
    to_csv_bytes,
    to_xlsx_bytes,
)


def test_safe_cell_escapes_dangerous_prefixes():
    for bad in ("=1+1", "+cmd", "-2+3", "@SUM(A1)", "\t=x", "\r=x", "=HYPERLINK(\"http://x\")"):
        assert safe_cell(bad).startswith("'")
    assert safe_cell("Normal Co") == "Normal Co"
    assert safe_cell(42.5) == 42.5 and safe_cell(None) == "" and safe_cell(True) == "yes"


ROW = {
    "company_id": 7, "name": "=cmd|' /C calc'!A0", "website": "https://synthetic.example/", "domain": "synthetic.example",
    "primary_industry": "robotics", "business_type": "manufacturer", "size_band": "small", "size_evidence": "-40 staff",
    "qualification_status": "qualified", "internship_category": "plausible_speculative", "identity_status": "verified",
    "location": "Dubai (map coordinates)", "total_score": 71.2, "suitability": 70.0, "evidence_confidence": 60.0,
    "contact_score": 80.0, "readiness_label": "Ready", "best_channel": "email", "best_contact": "@info@synthetic.example",
    "best_contact_name": None, "best_contact_role": None, "best_contact_role_category": "general",
    "contact_verification": "publicly_published", "contact_source_url": "https://synthetic.example/contact",
    "top_tasks": ["Supplier research"], "evidence_links": ["https://synthetic.example/about"], "sources": ["osm_overpass"],
    "outreach_status": "not_contacted", "follow_up_date": None, "last_researched_at": "2026-10-11T00:00:00+00:00",
}


def test_dataframe_columns_and_escaping():
    df = build_dataframe([ROW])
    assert list(df.columns) == [c for c, _ in EXPORT_COLUMNS]
    assert df["Company"][0].startswith("'=")
    assert df["Public email / official contact URL"][0].startswith("'@")
    assert df["Fit score"][0] == 71.2
    assert "Plausible speculative" in df["Internship suitability"][0]


def test_csv_and_xlsx_never_contain_formulas():
    df = build_dataframe([ROW])
    csv = to_csv_bytes(df).decode("utf-8-sig")
    assert "\n=cmd" not in csv and ",=cmd" not in csv
    wb = openpyxl.load_workbook(io.BytesIO(to_xlsx_bytes(df)))
    ws = wb.active
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            assert cell.data_type != "f"
