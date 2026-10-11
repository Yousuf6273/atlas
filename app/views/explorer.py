from __future__ import annotations

import pandas as pd
import streamlit as st
import ui

from atlas.db import jobs
from atlas.db.queries import prospect_rows
from atlas.export.exporter import build_dataframe, to_csv_bytes, to_xlsx_bytes
from atlas.models.enums import (
    INDUSTRY_LABELS,
    INTERNSHIP_LABELS,
    OUTREACH_LABELS,
    SIZE_LABELS,
)

ui.setup_page("Prospect Explorer", "Every researched company with its scores. Filter, sort by any dimension, open a "
                                   "profile, or export what you see.")
c = ui.conn()

job_list = jobs.list_jobs(c, 100)
job_opts = {0: "All companies"} | {j["id"]: f"#{j['id']} · {j['query_text'][:60]}" for j in job_list}
default_job = st.session_state.get("explorer_job", 0)
f1, f2, f3, f4 = st.columns([2, 1.2, 1.2, 1.2])
job_id = f1.selectbox("Search job", list(job_opts), index=list(job_opts).index(default_job) if default_job in job_opts else 0,
                      format_func=lambda k: job_opts[k])
st.session_state.explorer_job = job_id
rows = prospect_rows(c, job_id=job_id or None)
if not rows:
    ui.empty_state("No companies yet for this selection. Run a search first.")
    st.stop()

status_f = f2.multiselect("Qualification", ["qualified", "needs_review", "rejected", "not assessed"],
                          default=["qualified", "needs_review"])
intern_f = f3.multiselect("Internship suitability", list(INTERNSHIP_LABELS), format_func=lambda v: INTERNSHIP_LABELS[v])
size_f = f4.multiselect("Size", list(SIZE_LABELS), format_func=lambda v: SIZE_LABELS[v])
g1, g2, g3, g4 = st.columns([2, 1.2, 1.2, 1.2])
text = g1.text_input("Search name, website or task", "")
ind_f = g2.multiselect("Industry", list(INDUSTRY_LABELS), format_func=lambda v: INDUSTRY_LABELS[v])
contact_f = g3.selectbox("Contact channel", ["Any", "Has usable channel", "No usable channel"])
out_f = g4.multiselect("Outreach status", list(OUTREACH_LABELS), format_func=lambda v: OUTREACH_LABELS[v])
min_score = st.slider("Minimum fit score", 0, 100, 0)


def keep(r) -> bool:
    st_val = r["qualification_status"] or "not assessed"
    if status_f and st_val not in status_f:
        return False
    if intern_f and r["internship_category"] not in intern_f:
        return False
    if size_f and r["size_band"] not in size_f:
        return False
    if ind_f and r["primary_industry"] not in ind_f:
        return False
    if contact_f == "Has usable channel" and not r["has_usable_contact"]:
        return False
    if contact_f == "No usable channel" and r["has_usable_contact"]:
        return False
    if out_f and r["outreach_status"] not in out_f:
        return False
    if (r["total_score"] or 0) < min_score:
        return False
    if text:
        hay = f"{r['name']} {r['website'] or ''} {' '.join(r['top_tasks'])}".lower()
        if text.lower() not in hay:
            return False
    return True


filtered = [r for r in rows if keep(r)]
st.caption(f"{len(filtered)} of {len(rows)} companies shown")

df = pd.DataFrame([{
    "id": r["company_id"], "Company": r["name"], "Website": r["website"], "Industry": ui.label_industry(r["primary_industry"]),
    "Type": ui.label_btype(r["business_type"]), "Location": r["location"], "Size": ui.label_size(r["size_band"]),
    "Status": (r["qualification_status"] or "not assessed").replace("_", " "),
    "Internship": INTERNSHIP_LABELS.get(r["internship_category"] or "", "—"),
    "Fit": r["total_score"], "Suitability": r["suitability"], "Evidence": r["evidence_confidence"],
    "Contact": r["contact_score"], "Readiness": r["readiness_label"], "Tasks": r["n_tasks"],
    "Best contact": r["best_contact"] or "—", "Outreach": ui.label_outreach(r["outreach_status"]),
} for r in filtered])

prog = lambda label: st.column_config.ProgressColumn(label, min_value=0, max_value=100, format="%.0f")  # noqa: E731
event = st.dataframe(
    df.drop(columns=["id"]), hide_index=True, use_container_width=True, height=520, on_select="rerun",
    selection_mode="multi-row", key="explorer_table",
    column_config={"Website": st.column_config.LinkColumn(display_text=r"https?://(?:www\.)?([^/]+)"),
                   "Fit": prog("Fit score"), "Suitability": prog("Suitability"), "Evidence": prog("Evidence conf."),
                   "Contact": prog("Contact quality"), "Tasks": st.column_config.NumberColumn("Task ideas")})
selected_ids = [int(df.iloc[i]["id"]) for i in event.selection.rows] if len(df) else []

a, b, d, e = st.columns([1.2, 1.2, 1.2, 3])
if a.button("Open profile", disabled=len(selected_ids) != 1, type="primary"):
    ui.open_company(selected_ids[0])
export_ids = selected_ids or [r["company_id"] for r in filtered]
export_rows = [r for r in filtered if r["company_id"] in set(export_ids)]
xdf = build_dataframe(export_rows)
b.download_button("Export XLSX", to_xlsx_bytes(xdf), file_name="atlas_prospects.xlsx",
                  mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
d.download_button("Export CSV", to_csv_bytes(xdf), file_name="atlas_prospects.csv", mime="text/csv")
e.caption(f"Exports include {'the ' + str(len(selected_ids)) + ' selected' if selected_ids else 'all ' + str(len(filtered)) + ' filtered'} "
          "companies. Untrusted text is escaped so spreadsheets cannot execute it as formulas.")

with st.expander("How scores work"):
    st.markdown(
        "- **Fit score** – weighted sum of six rubric factors (weights in Settings): industry relevance, useful "
        "student tasks, internship accessibility, contact quality, geography and evidence of activity.\n"
        "- **Suitability** – the same without contact quality: how good the company is for you.\n"
        "- **Evidence confidence** – how much was actually researched (pages, sourced claims, sources, identity check).\n"
        "- **Contact quality** – best usable channel; companies with only a contact form stay eligible.\n"
        "No score is a probability; each factor's rubric is shown on the company profile.")
