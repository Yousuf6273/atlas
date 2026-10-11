from __future__ import annotations

import html
from datetime import date

import pandas as pd
import streamlit as st
import ui

from atlas.db import jobs
from atlas.db import providers as pdb
from atlas.db.queries import follow_ups_due, overview_counts, prospect_rows

ui.setup_page("Overview", "Your internship prospecting pipeline at a glance — every number below is counted from "
                          "the local database, nothing is estimated.")
c = ui.conn()
k = overview_counts(c)

ui.kpi_row([
    ("Discovered companies", k["discovered"], f"{k['researched']} researched"),
    ("Qualified", k["qualified"], f"{k['rejected']} rejected"),
    ("Needs review", k["needs_review"], f"{k['review_open']} open review items"),
    ("Usable contact channel", k["usable_contact"], "email, form or careers page"),
])
st.write("")
ui.kpi_row([
    ("Drafts prepared", k["drafts"], f"{k['approved_drafts']} approved by you"),
    ("Companies contacted", k["contacted"], "recorded by you"),
    ("Replies", k["replied"], "recorded by you"),
    ("Opportunities", k["opportunities"], f"{k['confirmed_programmes']} confirmed school-student routes"),
])

if k["discovered"] == 0:
    st.write("")
    with st.container(border=True):
        st.markdown("#### Get started")
        st.markdown(
            "1. Open **Settings → Profile** and fill in your name, school and skills (drafts use only what you enter).\n"
            "2. Go to **Search** and describe what you want, e.g. *“Find 50 small robotics and mechanical engineering "
            "companies in Dubai that might accept a summer intern.”*\n"
            "3. Check the interpreted filters, start the search and watch progress. Results appear in the "
            "**Prospect Explorer** as companies are researched.")
        if st.button("Go to Search", type="primary"):
            st.switch_page("views/search.py")
    st.stop()

st.write("")
left, right = st.columns([3, 2], gap="large")
with left:
    st.markdown("#### Top prospects")
    rows = [r for r in prospect_rows(c) if r["qualification_status"] == "qualified"][:10]
    if not rows:
        ui.empty_state("No qualified companies yet. Run a search or review the 'needs review' queue.")
    else:
        df = pd.DataFrame([{
            "Company": r["name"], "Industry": ui.label_industry(r["primary_industry"]), "Score": r["total_score"],
            "Internship": (r["internship_category"] or "").replace("_", " "), "Contact": r["best_contact"] or "—",
            "Readiness": r["readiness_label"], "id": r["company_id"]} for r in rows])
        ev = st.dataframe(df.drop(columns=["id"]), hide_index=True, use_container_width=True, on_select="rerun",
                          selection_mode="single-row", key="ov_top",
                          column_config={"Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")})
        if ev.selection.rows:
            ui.open_company(int(df.iloc[ev.selection.rows[0]]["id"]))
        st.caption("Select a row to open the company profile.")
with right:
    st.markdown("#### Follow-ups due")
    due = follow_ups_due(c, date.today().isoformat())
    if due:
        for d in due[:8]:
            st.markdown(f"- **{d['name']}** — follow up by {d['follow_up_date']}")
    else:
        st.caption("Nothing due.")
    st.markdown("#### Recent searches")
    for j in jobs.list_jobs(c, 6):
        st.markdown(f"{ui.pill(j['status'], {'completed': 'green', 'running': 'blue', 'failed': 'red'}.get(j['status'], 'grey'))} "
                    f"**#{j['id']}** {html.escape(j['query_text'][:70])}<br><span class='muted'>{html.escape(j['message'] or '')}</span>",
                    unsafe_allow_html=True)
    st.markdown("#### Source health")
    for p in pdb.list_providers(c):
        if p["availability"] != "available":
            continue
        ok = p["last_error_at"] is None or (p["last_success_at"] or "") > (p["last_error_at"] or "")
        st.markdown(f"{ui.pill('OK' if ok else 'Issue', 'green' if ok else 'amber')} {p['display_name']} "
                    f"<span class='muted'>last success: {html.escape((p['last_success_at'] or 'never')[:16])}</span>",
                    unsafe_allow_html=True)
