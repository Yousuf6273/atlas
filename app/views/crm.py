from __future__ import annotations

import html
from datetime import date

import pandas as pd
import streamlit as st
import ui

from atlas.db import outreach
from atlas.db.queries import follow_ups_due, prospect_rows
from atlas.models.enums import OUTREACH_LABELS, OutreachStatus
from atlas.outreach.crm import OutreachBlocked, check_transition, record

ui.setup_page("Pipeline (CRM)", "Track every conversation. Statuses change only when you record them.")
c = ui.conn()

tab_board, tab_update, tab_due, tab_dnc = st.tabs(["Pipeline", "Update status", "Follow-ups", "Do-not-contact"])

rows = [r for r in prospect_rows(c) if r["qualification_status"] in ("qualified", "needs_review")
        or r["outreach_status"] not in ("not_contacted",)]

with tab_board:
    if not rows:
        ui.empty_state("No companies in the pipeline yet.")
    else:
        counts = pd.Series([r["outreach_status"] for r in rows]).value_counts()
        cols = st.columns(5)
        for i, s in enumerate(["not_contacted", "draft_prepared", "contacted", "replied", "interested"]):
            cols[i].metric(OUTREACH_LABELS[s], int(counts.get(s, 0)))
        flt = st.multiselect("Status", list(OUTREACH_LABELS), format_func=lambda v: OUTREACH_LABELS[v])
        text = st.text_input("Search")
        view = [r for r in rows if (not flt or r["outreach_status"] in flt) and (not text or text.lower() in r["name"].lower())]
        st.dataframe(pd.DataFrame([{
            "Company": r["name"], "Status": OUTREACH_LABELS.get(r["outreach_status"], r["outreach_status"]),
            "Last contacted": r["last_contacted"], "Follow-up": r["follow_up_date"], "Best contact": r["best_contact"],
            "Fit": r["total_score"], "Internship": (r["internship_category"] or "").replace("_", " ")} for r in view]),
            hide_index=True, width="stretch",
            column_config={"Fit": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")})

with tab_update:
    opts = {r["company_id"]: r["name"] for r in rows}
    if not opts:
        ui.empty_state("Nothing to update.")
    else:
        cid = st.selectbox("Company", list(opts), format_func=lambda i: opts[i])
        hist = outreach.company_history(c, cid)
        if hist:
            st.markdown("##### History")
            for h in hist:
                st.markdown(f"- **{h['event_date']}** — {OUTREACH_LABELS.get(h['status'], h['status'])}"
                            + (f" via {h['channel']}" if h["channel"] else "") + (f" → `{h['contact_value']}`" if h["contact_value"] else "")
                            + (f" · {h['notes']}" if h["notes"] else "") + (f" · outcome: {h['outcome']}" if h["outcome"] else ""))
                if h["message_snapshot"]:
                    with st.expander("Message sent"):
                        st.text(h["message_snapshot"])
        with st.form("status_form"):
            new = st.selectbox("New status", [s.value for s in OutreachStatus], format_func=lambda v: OUTREACH_LABELS[v])
            ev_date = st.date_input("Date", date.today())
            fu = st.date_input("Follow-up date (optional)", value=None)
            notes = st.text_area("Notes", height=80)
            outcome = st.text_input("Internship outcome (optional)")
            contact_value = st.text_input("Contact used (for 'contacted')")
            confirm = st.checkbox("I confirm this happened (e.g. I sent the message myself)")
            if st.form_submit_button("Record"):
                status = OutreachStatus(new)
                chk = check_transition(c, cid, status, contact_value=contact_value or None)
                if not chk.allowed:
                    st.error(chk.reason)
                elif status == OutreachStatus.CONTACTED and not confirm:
                    st.error("Only record 'Contacted' after you have actually sent the message.")
                elif chk.requires_confirmation and not confirm:
                    st.warning(f"{chk.reason} Tick the confirmation box to record anyway.")
                else:
                    try:
                        record(c, cid, status, confirmed=True, contact_value=contact_value or None,
                               event_date=ev_date.isoformat(), follow_up_date=fu.isoformat() if fu else None,
                               notes=notes or None, outcome=outcome or None)
                        st.success("Recorded.")
                        st.rerun()
                    except OutreachBlocked as exc:
                        st.error(str(exc))

with tab_due:
    due = follow_ups_due(c, date.today().isoformat())
    if not due:
        st.caption("No follow-ups due today.")
    for d in due:
        st.markdown(f"- **{d['name']}** — follow-up was due {d['follow_up_date']} (contacted {d['event_date']})")
    st.caption("Use the 'Polite follow-up' draft in Outreach Studio.")

with tab_dnc:
    st.markdown("Companies, domains or addresses on this list are never offered for outreach. Respect every opt-out.")
    with st.form("dnc_add"):
        dom = st.text_input("Domain (e.g. company.ae)")
        em = st.text_input("Email address")
        reason = st.text_input("Reason")
        if st.form_submit_button("Add") and (dom or em):
            outreach.add_dnc(c, domain=dom or None, email=em or None, reason=reason or "opt-out")
            st.rerun()
    for row in outreach.list_dnc(c):
        cols = st.columns([4, 1])
        cols[0].markdown(f"- {html.escape(row['company_name'] or row['domain'] or row['email'])} — {html.escape(row['reason'] or '')} "
                         f"<span class='muted'>({row['added_at'][:10]})</span>", unsafe_allow_html=True)
        if cols[1].button("Remove", key=f"dnc_{row['id']}"):
            outreach.remove_dnc(c, row["id"])
            st.rerun()
