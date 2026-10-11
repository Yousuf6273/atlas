from __future__ import annotations

import html

import pandas as pd
import streamlit as st
import ui

from atlas.assess.dossier import load_dossier
from atlas.contacts.quality import best_contact
from atlas.db import assessments, companies, jobs, outreach, research
from atlas.db.core import loads
from atlas.db.queries import company_job_ids
from atlas.models.enums import (
    INTERNSHIP_LABELS,
    QualificationStatus,
    SizeBand,
)
from atlas.models.search import SearchCriteria
from atlas.outreach.crm import current_status
from atlas.pipeline.runner import is_running, start_company_refresh

ui.setup_page("Company Profile", "Everything ATLAS knows about one company — each claim links to the page it came from.")
c = ui.conn()

opts = ui.company_options(c)
if not opts:
    ui.empty_state("No companies yet.")
    st.stop()
sel = st.session_state.get("selected_company")
ids = list(opts)
cid = st.selectbox("Company", ids, index=ids.index(sel) if sel in ids else 0, format_func=lambda i: opts[i])
st.session_state.selected_company = cid

d = load_dossier(c, cid)
comp = d.company
assessed_jobs = [j for j in assessments.current_assessment_jobs(c, cid) if j is not None]
job_ctx = None
if len(assessed_jobs) > 1:
    labels = {j: f"Search #{j} · {jobs.get_job(c, j)['query_text'][:60]}" for j in sorted(assessed_jobs, reverse=True)}
    job_ctx = st.selectbox("Assessed against", list(labels), format_func=lambda j: labels[j],
                           help="Relevance and scores depend on what each search asked for.")
elif assessed_jobs:
    job_ctx = assessed_jobs[0]
q = assessments.current_qualification(c, cid, job_ctx)
a = assessments.current_internship(c, cid)
tasks = assessments.current_tasks(c, cid)
score = assessments.current_score(c, cid, job_ctx)

# ---------------------------------------------------------------- header
with st.container(border=True):
    h1, h2 = st.columns([3, 1])
    site = comp.get("website")
    h1.markdown(f"### {html.escape(comp['name'])}")
    h1.markdown(
        " ".join([ui.status_pill(q.status.value if q else None), ui.internship_pill(comp.get("internship_category")),
                  ui.pill(f"Identity: {comp.get('identity_status')}", {"verified": "green", "mismatch": "red"}.get(
                      comp.get("identity_status"), "grey")),
                  ui.pill(ui.label_outreach(current_status(c, cid).value), "blue")]), unsafe_allow_html=True)
    if site:
        h1.markdown(f"[{site}]({site})")
    if comp.get("identity_notes"):
        h1.caption(comp["identity_notes"])
    if score:
        h2.metric("Fit score", f"{score.total:.0f}/100")
        h2.caption(f"Suitability {score.suitability:.0f} · evidence {score.evidence_confidence:.0f} · "
                   f"readiness: {score.readiness_label}")
    else:
        h2.caption("Not assessed yet.")

running = is_running(f"company:{cid}")
act = st.columns([1.3, 1.3, 1.3, 3])
if act[0].button("Re-research now", disabled=running or not site, help="Re-crawl the website and re-run all assessments"):
    jids = company_job_ids(c, cid)
    crit = jobs.job_criteria(c, jids[0]) if jids else SearchCriteria(raw_query="manual refresh")
    start_company_refresh(ui.base_config(), cid, crit)
    st.toast("Research started in the background — refresh in a minute.")
if act[1].button("Open in Outreach Studio", disabled=q is None or q.status == QualificationStatus.REJECTED):
    st.session_state.outreach_company = cid
    st.switch_page("views/outreach.py")
if act[2].button("Contact research"):
    st.session_state.contacts_company = cid
    st.switch_page("views/contacts.py")
if running:
    act[3].info("Research running in the background…")

if q is None:
    st.warning("This company has been discovered but not yet researched/assessed.")

tab_sum, tab_tasks, tab_evid, tab_src, tab_edit = st.tabs(
    ["Summary", f"Task ideas ({len(tasks)})", "Evidence", "Sources & history", "Corrections"])

# ---------------------------------------------------------------- summary
with tab_sum:
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("##### What the company does")
        acts = d.claims_of("activity")
        seen = set()
        shown = 0
        for cl in acts:
            key = cl["value"][:120].lower()
            if key in seen:
                continue
            seen.add(key)
            st.markdown(ui.evidence_block(cl["value"], cl["source_url"], cl["retrieved_at"]), unsafe_allow_html=True)
            if cl["extractor"].startswith("llm:"):
                st.caption("↑ summarised by local AI; the quote above was verified verbatim on the page")
            shown += 1
            if shown >= 5:
                break
        if not shown:
            st.caption("No activity statements extracted (website not researched or uninformative).")
        offs = list(dict.fromkeys(cl["value"] for cl in d.claims_of("offering")))[:10]
        if offs:
            st.markdown("**Products / services named on the site:** " + ", ".join(offs))
        if q:
            st.markdown("##### Qualification")
            for r in q.reasons:
                st.markdown(f"- {r}")
            st.markdown(f"**Location:** {q.geo_status.value.replace('_', ' ')} — " + "; ".join(q.geo_evidence[:3]))
            st.markdown(f"**Size:** {ui.label_size(q.size_band.value)}" + (f" — {q.size_evidence}" if q.size_evidence else
                                                                         " — no public figure found"))
            st.markdown(f"**Activity:** {q.operational_status.value.replace('_', ' ')} — " +
                        ("; ".join(q.operational_indicators) or "no indicators"))
            if q.uncertainties:
                st.markdown("**Uncertainties**")
                for u in q.uncertainties:
                    st.markdown(f"- {u}")
    with right:
        if score:
            st.markdown("##### Score breakdown")
            st.markdown(ui.factor_bars([f.model_dump() for f in score.factors]), unsafe_allow_html=True)
            for n in score.notes:
                st.caption(n)
        if a:
            st.markdown("##### Internship suitability")
            st.markdown(ui.internship_pill(a.category.value), unsafe_allow_html=True)
            for r in a.reasons:
                st.markdown(f"- {r}")
            for ev in a.evidence[:3]:
                st.markdown(ui.evidence_block(ev.snippet, ev.source_url, ev.retrieved_at), unsafe_allow_html=True)
            if a.unknowns:
                st.caption("Unknown: " + "; ".join(a.unknowns))
            for f in a.safety_flags:
                st.warning("Safety: " + f)
        if q:
            st.markdown("##### Recommended approach")
            pick, why = best_contact(d.contacts, q.size_band, has_programme=bool(a and a.has_programme))
            st.write(why)
            if pick:
                who = f"{pick['name']} ({pick['role']})" if pick.get("name") else (pick.get("role") or pick["role_category"].replace("_", " "))
                st.markdown(f"**Use:** `{pick['value'][:90]}` — {pick['channel'].replace('_', ' ')} · {who}")
                st.caption(f"Status: {ui.label_email(pick['verification_status']) if pick['channel']=='email' else 'official channel'}"
                           f" · source: {pick['source_url'] or pick['source_type']}")
            injected = d.claims_of("prompt_injection_text")
            if injected:
                st.warning(f"This website contains {len(injected)} instruction-like passage(s) aimed at automated tools; "
                           "they were ignored.")

# ---------------------------------------------------------------- tasks
with tab_tasks:
    if not tasks:
        st.caption("No task ideas: the company's own text did not support any template, or the website was not researched.")
    for t in tasks:
        with st.container(border=True):
            st.markdown(f"**{t.title}** " + ui.pill("Observed need" if t.basis.value == "observed_need" else
                                                    "Inferred opportunity", "green" if t.basis.value == "observed_need" else "blue")
                        + ui.pill(f"confidence {t.confidence.value}", "grey")
                        + ui.pill(f"difficulty {t.difficulty.value}", "grey")
                        + ui.pill(f"supervision {t.supervision.value}", "amber" if t.supervision.value == "required" else "grey"),
                        unsafe_allow_html=True)
            st.write(t.description)
            st.caption("Why: " + t.rationale)
            st.caption("Skills: " + ", ".join(t.skills) + f" · your skill match {t.skill_match:.0%}")
            st.caption("Outreach angle: " + t.outreach_angle)
            if t.safety_note:
                st.caption("Safety: " + t.safety_note)
            for ev in t.evidence[:3]:
                st.markdown(ui.evidence_block(ev.snippet, ev.source_url, ev.retrieved_at), unsafe_allow_html=True)

# ---------------------------------------------------------------- evidence
with tab_evid:
    types = sorted({cl["claim_type"] for cl in d.claims})
    pick_types = st.multiselect("Claim types", types, default=[t for t in types if t in (
        "activity", "offering", "internship_signal", "eligibility_school_ok", "eligibility_university_only",
        "eligibility_min_age", "hiring_role", "size_statement", "engineering_team", "legal_entity", "site_status")])
    rows = [cl for cl in d.claims if cl["claim_type"] in pick_types]
    st.dataframe(pd.DataFrame([{"Type": cl["claim_type"], "Value": cl["value"][:120], "Evidence": cl["evidence_snippet"][:200],
                                "Source": cl["source_url"], "Retrieved": cl["retrieved_at"][:16],
                                "Extractor": cl["extractor"], "Inference": bool(cl["is_inference"])} for cl in rows]),
                 hide_index=True, width="stretch", column_config={"Source": st.column_config.LinkColumn()})
    st.caption("Every claim is a statement about what a source says, with the URL and retrieval time. "
               "Inferences are labelled.")

# ---------------------------------------------------------------- sources & history
with tab_src:
    st.markdown("##### Discovery provenance")
    st.dataframe(pd.DataFrame([{"Provider": s["provider"], "Record": s["external_id"], "Name in source": s["raw_name"],
                                "Source URL": s["source_url"], "Retrieved": s["retrieved_at"][:16]} for s in d.sources]),
                 hide_index=True, width="stretch", column_config={"Source URL": st.column_config.LinkColumn()})
    if d.locations:
        st.markdown("##### Locations")
        st.dataframe(pd.DataFrame([{k: loc[k] for k in ("address", "city", "latitude", "longitude", "location_type",
                                                          "source_provider")} for loc in d.locations]),
                     hide_index=True, width="stretch")
    st.markdown("##### Pages researched")
    pages = research.company_pages(c, cid, with_text=False)
    st.dataframe(pd.DataFrame([{"URL": p["url"], "Type": p["page_type"], "HTTP": p["status_code"], "Words": p["word_count"],
                                "Duplicate": bool(p["is_duplicate"]), "Error": p["error"], "Fetched": p["fetched_at"][:16]}
                               for p in pages]), hide_index=True, width="stretch",
                 column_config={"URL": st.column_config.LinkColumn()})
    st.markdown("##### Field history (values are never silently overwritten)")
    st.dataframe(pd.DataFrame(companies.field_history(c, cid)), hide_index=True, width="stretch")
    hist = assessments.assessment_history(c, cid)
    if hist["qualification"]:
        st.markdown("##### Assessment history")
        st.dataframe(pd.DataFrame(hist["qualification"]), hide_index=True, width="stretch")

# ---------------------------------------------------------------- corrections
with tab_edit:
    st.markdown("Corrections are stored with source **user** (highest confidence) and kept in the field history.")
    with st.form("corr"):
        new_site = st.text_input("Official website", comp.get("website") or "")
        new_size = st.selectbox("Size band", [s.value for s in SizeBand], index=[s.value for s in SizeBand].index(
            comp.get("size_band") or "unknown"), format_func=ui.label_size)
        size_ev = st.text_input("Size evidence (what you know and where from)", comp.get("size_evidence") or "")
        status_override = st.selectbox("Qualification override", ["(keep)", "qualified", "needs_review", "rejected"])
        reason = st.text_input("Reason for override")
        if st.form_submit_button("Save corrections"):
            from atlas.resolve.normalize import registrable_domain, website_root

            if new_site and new_site != comp.get("website"):
                root = website_root(new_site)
                if root:
                    companies.set_field(c, cid, "website", root, confidence=1.0, source="user")
                    companies.set_field(c, cid, "domain", registrable_domain(root), confidence=1.0, source="user")
            if new_size != comp.get("size_band"):
                companies.set_field(c, cid, "size_band", new_size, confidence=1.0, source="user")
                companies.set_field(c, cid, "size_evidence", size_ev or "set manually", confidence=1.0, source="user")
            if status_override != "(keep)" and q is not None:
                q2 = q.model_copy(update={"status": QualificationStatus(status_override), "method": "manual",
                                          "reasons": [f"Manual override: {reason or 'no reason given'}"] + q.reasons})
                assessments.save_qualification(c, cid, None, q2)
                companies.mark_researched(c, cid, qualification_status=status_override)
            st.success("Saved. Use 'Re-research now' to re-run assessments with the corrected data.")
    st.markdown("##### Do not contact")
    if outreach.is_dnc(c, company_id=cid):
        st.info("This company is on your do-not-contact list (manage in Pipeline → Do-not-contact).")
    elif st.button("Add to do-not-contact list"):
        outreach.add_dnc(c, company_id=cid, domain=comp.get("domain"), reason="added from company profile")
        st.rerun()
    st.caption(f"Internship categories: {', '.join(INTERNSHIP_LABELS.values())}.")
    _ = loads
