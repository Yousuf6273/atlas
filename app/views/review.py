from __future__ import annotations

import pandas as pd
import streamlit as st
import ui

from atlas.assess.dossier import load_dossier
from atlas.db import assessments, companies, jobs, review
from atlas.evaluation.metrics import compute_metrics, review_sample
from atlas.models.enums import QualificationStatus

ui.setup_page("Review & Evaluation", "Resolve uncertain matches and borderline companies, and measure how accurate "
                                     "ATLAS is on your data.")
c = ui.conn()

tab_dup, tab_qual, tab_eval = st.tabs([
    f"Possible duplicates ({len(review.open_items(c, 'possible_duplicate'))})",
    f"Needs review ({len(review.open_items(c, 'qualification')) + len(review.open_items(c, 'identity'))})",
    "Evaluation"])


def side(cid: int) -> None:
    comp = companies.get_company(c, cid)
    st.markdown(f"**{comp['name']}**")
    st.caption(f"Website: {comp['website'] or '—'} · id {cid}")
    for s in companies.company_sources(c, cid):
        st.caption(f"• {s['provider']}: “{s['raw_name']}” {s['source_url'] or ''}")
    for loc in companies.company_locations(c, cid)[:3]:
        st.caption(f"• location: {loc['address'] or loc['city'] or ''} {loc['latitude'] or ''},{loc['longitude'] or ''}")


with tab_dup:
    items = review.open_items(c, "possible_duplicate")
    if not items:
        st.caption("No uncertain matches. Ambiguous records are never merged automatically; they appear here.")
    for it in items:
        with st.container(border=True):
            st.markdown(f"**{it['reason']}** · similarity {it['score'] or 0:.0f} · "
                        f"suggested relationship: {it['details'].get('relationship', '?').replace('_', ' ')}")
            a, b = st.columns(2)
            with a:
                side(it["company_id"])
            with b:
                side(it["other_company_id"])
            x = st.columns(4)
            if x[0].button("Same company — keep left", key=f"ml_{it['id']}"):
                companies.merge_companies(c, it["company_id"], it["other_company_id"], reason="manual review")
                st.rerun()
            if x[1].button("Same company — keep right", key=f"mr_{it['id']}"):
                companies.merge_companies(c, it["other_company_id"], it["company_id"], reason="manual review")
                st.rerun()
            if x[2].button("Related (branch/parent), keep separate", key=f"rel_{it['id']}"):
                review.resolve(c, it["id"], resolution="related_entities_kept_separate")
                st.rerun()
            if x[3].button("Different companies", key=f"diff_{it['id']}"):
                review.resolve(c, it["id"], resolution="distinct", status="dismissed")
                st.rerun()

with tab_qual:
    items = review.open_items(c, "qualification") + review.open_items(c, "identity")
    if not items:
        st.caption("Nothing to review.")
    for it in items:
        with st.container(border=True):
            st.markdown(f"**{it['company_name']}** · {it['item_type']} — {it['reason']}")
            unc = it["details"].get("uncertainties") or []
            if unc:
                st.caption("Uncertain: " + "; ".join(unc))
            x = st.columns(4)
            q = assessments.current_qualification(c, it["company_id"])
            if x[0].button("Open profile", key=f"op_{it['id']}"):
                ui.open_company(it["company_id"])
            for col, status, label in ((x[1], "qualified", "Mark qualified"), (x[2], "rejected", "Mark rejected")):
                if col.button(label, key=f"{status}_{it['id']}") and q is not None:
                    q2 = q.model_copy(update={"status": QualificationStatus(status), "method": "manual",
                                              "reasons": [f"Manual review: marked {status}"] + q.reasons})
                    assessments.save_qualification(c, it["company_id"], None, q2)
                    companies.mark_researched(c, it["company_id"], qualification_status=status)
                    review.resolve(c, it["id"], resolution=f"manual_{status}")
                    st.rerun()
            if x[3].button("Dismiss", key=f"dis_{it['id']}"):
                review.resolve(c, it["id"], resolution="dismissed", status="dismissed")
                st.rerun()

with tab_eval:
    st.markdown("Review the first **25–50** companies of a real search to measure discovery relevance, duplicates, "
                "classification accuracy, contact validity, unsupported claims, internship assessments and outreach "
                "usefulness. Metrics are computed only from these reviews.")
    jl = jobs.list_jobs(c, 50)
    if not jl:
        ui.empty_state("Run a search first.")
        st.stop()
    jopts = {j["id"]: f"#{j['id']} · {j['query_text'][:70]}" for j in jl}
    job_id = st.selectbox("Job", list(jopts), format_func=lambda k: jopts[k])
    n = st.slider("Sample size", 10, 50, 25, step=5)
    sample = review_sample(c, job_id, n)
    done = {e["company_id"] for e in review.evaluations(c, job_id)}
    st.progress(len(done & set(sample)) / max(1, len(sample)), text=f"{len(done & set(sample))}/{len(sample)} reviewed")
    if sample:
        pending = [x for x in sample if x not in done] or sample
        cid = st.selectbox("Company to review", pending, format_func=lambda i: companies.get_company(c, i)["name"])
        d = load_dossier(c, cid)
        q = assessments.current_qualification(c, cid)
        a = assessments.current_internship(c, cid)
        with st.container(border=True):
            st.markdown(f"**{d.name}** — {d.company.get('website') or 'no website'}")
            if q:
                st.caption(f"ATLAS: {q.status.value} · industry {q.primary_industry} · relevance {q.target_relevance:.0f} · "
                           f"type {q.business_type.value} · size {q.size_band.value}")
            if a:
                st.caption(f"Internship: {a.category.value} — {'; '.join(a.reasons)[:200]}")
            for cl in d.claims_of("activity")[:3]:
                st.markdown(ui.evidence_block(cl["value"], cl["source_url"]), unsafe_allow_html=True)
            best = [x for x in d.contacts if x["quality_score"] > 0][:2]
            for x in best:
                st.caption(f"Contact: {x['value']} ({x['channel']}) — source {x['source_url']}")
        prev = next((e for e in review.evaluations(c, job_id) if e["company_id"] == cid), {})

        def yn(label, key):
            opts = ["not checked", "yes", "no"]
            v = prev.get(key)
            idx = 0 if v is None else (1 if v else 2)
            r = st.radio(label, opts, index=idx, horizontal=True, key=f"ev_{key}_{cid}")
            return None if r == "not checked" else r == "yes"

        with st.form(f"eval_{cid}"):
            vals = {
                "discovery_relevant": yn("Is this company genuinely relevant to the search?", "discovery_relevant"),
                "is_duplicate": yn("Is it a duplicate of another record?", "is_duplicate"),
                "industry_correct": yn("Is the industry classification correct?", "industry_correct"),
                "contact_source_valid": yn("Is the contact really published at the cited source?", "contact_source_valid"),
                "internship_assessment_correct": yn("Is the internship assessment reasonable?", "internship_assessment_correct"),
                "outreach_useful": yn("Would the outreach recommendation be useful?", "outreach_useful"),
            }
            cc = st.columns(2)
            vals["claims_checked"] = cc[0].number_input("Claims you checked", 0, 50, int(prev.get("claims_checked") or 0))
            vals["unsupported_claims"] = cc[1].number_input("…of which not supported by the source", 0, 50,
                                                            int(prev.get("unsupported_claims") or 0))
            vals["notes"] = st.text_input("Notes", prev.get("notes") or "")
            if st.form_submit_button("Save review", type="primary"):
                review.save_evaluation(c, cid, job_id, vals)
                st.rerun()
    st.markdown("##### Metrics for this job")
    st.dataframe(pd.DataFrame([{"Metric": m.label, "Result": m.fmt()} for m in compute_metrics(c, job_id)]),
                 hide_index=True, use_container_width=True)
