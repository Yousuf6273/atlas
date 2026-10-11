from __future__ import annotations

import asyncio
from datetime import date, timedelta

import streamlit as st
import ui

from atlas.assess.dossier import load_dossier
from atlas.contacts.quality import best_contact
from atlas.db import assessments, outreach
from atlas.llm.client import OllamaClient
from atlas.llm.tasks import llm_polish_draft
from atlas.models.enums import DRAFT_LABELS, OutreachStatus
from atlas.outreach.crm import OutreachBlocked, check_transition, current_status, record
from atlas.outreach.generator import generate_drafts

ui.setup_page("Outreach Studio", "Personalised drafts built only from verified company facts and your profile. "
                                 "Review, edit and approve each one — ATLAS never sends anything.")
c = ui.conn()
prof = ui.profile()
missing = prof.is_complete_for_outreach()
if missing:
    st.warning("Your profile is missing: " + ", ".join(missing) + ". Drafts show [placeholders] until you fill these in "
               "under Settings → Profile.", icon=":material/person:")

opts = ui.company_options(c, "AND qualification_status IN ('qualified','needs_review')")
if not opts:
    ui.empty_state("No qualified or review-stage companies yet.")
    st.stop()
sel = st.session_state.get("outreach_company")
ids = list(opts)
cid = st.selectbox("Company", ids, index=ids.index(sel) if sel in ids else 0, format_func=lambda i: opts[i])
st.session_state.outreach_company = cid

blocked = outreach.is_dnc(c, company_id=cid)
status = current_status(c, cid)
st.markdown(f"Current status: {ui.pill(ui.label_outreach(status.value), 'blue')}", unsafe_allow_html=True)
if blocked:
    st.error(f"Do not contact: {blocked}")
    st.stop()

d = load_dossier(c, cid)
q = assessments.current_qualification(c, cid)
a = assessments.current_internship(c, cid)
tasks = assessments.current_tasks(c, cid)
pick, strategy = best_contact(d.contacts, q.size_band, has_programme=bool(a and a.has_programme)) if q else (None, "")
st.caption("Suggested channel: " + (f"`{pick['value']}` ({pick['channel'].replace('_', ' ')})" if pick else "none found")
           + f" — {strategy}")

cols = st.columns([1.4, 1.6, 4])
if cols[0].button("Regenerate drafts", help="Rebuild from the latest research. Approved drafts are kept."):
    for dr in generate_drafts(d, q, a, tasks, prof, pick):
        outreach.save_draft(c, cid, kind=dr.kind.value, subject=dr.subject, body=dr.body,
                            facts=[f.as_dict() for f in dr.facts] + [{"kind": "warning", "text": w} for w in dr.warnings],
                            method="template", contact_id=pick["id"] if pick else None)
    st.rerun()

drafts = outreach.company_drafts(c, cid)
if not drafts:
    st.info("No drafts yet — click 'Regenerate drafts'.")
    st.stop()

by_kind: dict[str, dict] = {}
for dr in drafts:  # latest per kind
    by_kind[dr["kind"]] = dr
tabs = st.tabs([DRAFT_LABELS[k] for k in DRAFT_LABELS if k in by_kind])
cfg = ui.cfg()
for tab, kind in zip(tabs, [k for k in DRAFT_LABELS if k in by_kind]):
    dr = by_kind[kind]
    with tab:
        left, right = st.columns([3, 2], gap="large")
        with left:
            subject = st.text_input("Subject", dr["subject"] or "", key=f"subj_{dr['id']}") if dr["subject"] is not None else None
            body = st.text_area("Message", dr["final_body"], height=360, key=f"body_{dr['id']}")
            if kind == "networking_message":
                st.caption(f"{len(body)}/300 characters")
            b = st.columns(4)
            if b[0].button("Save edits", key=f"save_{dr['id']}"):
                outreach.update_draft_text(c, dr["id"], subject=subject, edited_body=body)
                st.toast("Saved")
            approved = bool(dr["approved"])
            if b[1].button("Unapprove" if approved else "Approve", key=f"appr_{dr['id']}", type="secondary" if approved else "primary"):
                outreach.update_draft_text(c, dr["id"], subject=subject, edited_body=body)
                outreach.set_draft_approved(c, dr["id"], not approved)
                st.rerun()
            if cfg.llm.enabled and cfg.llm.use_for_draft_polish and b[2].button("Polish (local AI)", key=f"pol_{dr['id']}"):
                facts_text = " ".join(f.get("text", "") for f in dr["facts"]) + " " + prof.model_dump_json()
                new, notes = asyncio.run(llm_polish_draft(OllamaClient(cfg.llm, c), body, facts_text))
                if new:
                    outreach.update_draft_text(c, dr["id"], subject=subject, edited_body=new)
                    st.rerun()
                else:
                    st.warning("; ".join(notes))
            st.markdown("**Copy**")
            st.code(((f"Subject: {subject}\n\n" if subject else "") + body), language=None, wrap_lines=True)
        with right:
            st.markdown("##### Facts used in this draft")
            for f in dr["facts"]:
                if f.get("kind") == "warning":
                    st.warning(f["text"])
                    continue
                src = f" · [source]({f['source_url']})" if f.get("source_url") else ""
                note = f" · {f['note']}" if f.get("note") else ""
                st.markdown(f"- *{f['kind']}*: {f['text'][:160]}{src}{note}")
            st.caption("Check each fact before sending. Edit anything that does not sound like you.")
            st.markdown(f"Approved: **{'yes' if dr['approved'] else 'no'}**")

st.divider()
st.markdown("#### Record that you sent a message")
with st.form("record_sent"):
    kinds = [k for k in DRAFT_LABELS if k in by_kind]
    which = st.selectbox("Message used", kinds, format_func=lambda k: DRAFT_LABELS[k])
    channel_value = st.text_input("Sent to (email / form URL)", pick["value"] if pick else "")
    sent_on = st.date_input("Date sent", date.today())
    follow = st.date_input("Follow-up date", date.today() + timedelta(days=7))
    confirm = st.checkbox("I have personally sent this message (ATLAS did not send it).")
    allow_dup = st.checkbox("Record even if this company was already contacted (e.g. a follow-up)")
    if st.form_submit_button("Record as contacted"):
        dr = by_kind[which]
        chk = check_transition(c, cid, OutreachStatus.CONTACTED, contact_value=channel_value or None)
        if not dr["approved"]:
            st.error("Approve the draft first — every message must be reviewed before sending.")
        elif not confirm:
            st.error("Tick the confirmation box: only record messages you actually sent.")
        elif chk.requires_confirmation and not allow_dup:
            st.warning(chk.reason)
        else:
            try:
                record(c, cid, OutreachStatus.CONTACTED, confirmed=True, contact_value=channel_value or None,
                       channel=pick["channel"] if pick else None, draft_id=dr["id"],
                       message_snapshot=dr["final_body"], event_date=sent_on.isoformat(),
                       follow_up_date=follow.isoformat())
                st.success("Recorded. Track replies in Pipeline (CRM).")
            except OutreachBlocked as exc:
                st.error(str(exc))
