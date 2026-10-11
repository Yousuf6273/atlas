from __future__ import annotations

import asyncio
import html

import pandas as pd
import streamlit as st
import ui

from atlas.db import jobs
from atlas.db import providers as pdb
from atlas.db.core import transaction
from atlas.db.queries import provider_run_summary
from atlas.geo.areas import AREAS
from atlas.llm.client import OllamaClient
from atlas.llm.tasks import llm_refine_query
from atlas.models.enums import INDUSTRY_LABELS, BusinessType, Industry, SizeBand
from atlas.models.search import ParsedQuery, SearchCriteria
from atlas.net.fetcher import Fetcher
from atlas.pipeline.ingest import ingest_candidate
from atlas.pipeline.runner import start_job
from atlas.providers import registry
from atlas.providers.base import ProviderContext, ProviderError
from atlas.providers.manual import (
    directory_page_candidates,
    parse_tabular,
    rows_to_candidates,
    urls_to_candidates,
)
from atlas.query import parse_query

ui.setup_page("Search", "Describe the companies you want in plain English. ATLAS shows how it understood the request "
                        "before running anything.")
c = ui.conn()
cfg = ui.cfg()

EXAMPLES = [
    "Find 50 small mechanical engineering and robotics companies in Dubai that might accept a summer intern.",
    "Find AI startups in DIFC where I could help with Python automation.",
    "Find aerospace-related companies in the UAE with engineering teams.",
    "Find 50 manufacturing companies where I could offer Excel automation or supplier research.",
]

tab_search, tab_import, tab_jobs, tab_sources = st.tabs(["New search", "Import companies", "Search history",
                                                          "Data sources"])

def _default_area() -> str:
    from atlas.db.settings import get_setting

    key = get_setting(c, "default_area", "dubai")
    return key if key in AREAS else "dubai"


# ------------------------------------------------------------------ new search
with tab_search:
    def _use_example() -> None:
        idx = st.session_state.get("ex_idx", 0) % len(EXAMPLES)
        st.session_state.ex_idx = idx + 1
        st.session_state.query_text = EXAMPLES[idx]
        st.session_state.parsed = parse_query(EXAMPLES[idx], default_area=_default_area()).model_dump(mode="json")

    q = st.text_area("What are you looking for?", key="query_text", height=80, placeholder=EXAMPLES[0])
    cols = st.columns([1, 1, 4])
    if cols[0].button("Interpret", type="primary", use_container_width=True):
        if q.strip():
            st.session_state.parsed = parse_query(q, default_area=_default_area()).model_dump(mode="json")
    cols[1].button("Try an example", use_container_width=True, on_click=_use_example)

    if "parsed" in st.session_state:
        parsed = ParsedQuery.model_validate(st.session_state.parsed)
        crit = parsed.criteria
        with st.container(border=True):
            st.markdown("##### Interpreted search — confirm or edit")
            badge = "deterministic parser" if parsed.method == "deterministic" else "deterministic parser + local AI"
            st.caption(f"Interpreted by: {badge}")
            for n in parsed.notes:
                st.markdown(f"- {n}")
            if parsed.ambiguity >= 0.4 and cfg.llm.enabled and cfg.llm.use_for_query_parsing:
                if st.button("Ask local AI to suggest missing filters"):
                    async def _refine():
                        client = OllamaClient(cfg.llm, c)
                        return await llm_refine_query(client, parsed)
                    refined = asyncio.run(_refine())
                    if refined.method == parsed.method:
                        st.warning("Local AI not available or had no suggestions — edit the filters manually.")
                    else:
                        st.session_state.parsed = refined.model_dump(mode="json")
                        st.rerun()
            a, b = st.columns(2)
            industries = a.multiselect("Industries", options=[i.value for i in Industry],
                                       default=[i.value for i in crit.industries],
                                       format_func=lambda v: INDUSTRY_LABELS[v])
            area_keys = b.multiselect("Locations", options=list(AREAS), default=[x.key for x in crit.areas],
                                      format_func=lambda k: AREAS[k].label + (" (approx. zone)" if AREAS[k].approximate else ""))
            a, b, d = st.columns(3)
            sizes = a.multiselect("Company size preference", options=[s.value for s in SizeBand if s != SizeBand.UNKNOWN],
                                  default=[s.value for s in crit.size_preference], format_func=ui.label_size,
                                  help="A preference used for ranking notes; companies of unknown size are kept.")
            desired = b.number_input("Target number of qualified companies", 1, 500, int(crit.desired_count),
                                     help="A goal, not a quota: standards are never lowered to reach it.")
            internship_type = d.selectbox("Internship type", ["summer", "short_placement", "project", "any"],
                                          index=["summer", "short_placement", "project", "any"].index(crit.internship_type))
            a, b = st.columns(2)
            keywords = a.text_input("Extra keywords (comma-separated)", ", ".join(crit.keywords))
            excl_kw = b.text_input("Excluded keywords", ", ".join(crit.excluded_keywords))
            a, b = st.columns(2)
            excl_ind = a.multiselect("Excluded industries", [i.value for i in Industry],
                                     default=[i.value for i in crit.excluded_industries],
                                     format_func=lambda v: INDUSTRY_LABELS[v])
            excl_bt = b.multiselect("Excluded business types", [x.value for x in BusinessType],
                                    default=[x.value for x in crit.excluded_business_types], format_func=ui.label_btype)
            a, b, d = st.columns(3)
            skills = a.text_input("Skills you want to offer", ", ".join(crit.skills))
            req_eng = b.checkbox("Require evidence of an engineering team", crit.require_engineering_team)
            run_providers = d.multiselect(
                "Sources", [p.name for p in registry.IMPLEMENTED if p.info.runs_in_search],
                default=crit.providers, help="Empty = all sources enabled in Settings.")
            final = SearchCriteria(
                raw_query=crit.raw_query, areas=[AREAS[k] for k in area_keys],
                industries=[Industry(i) for i in industries], excluded_industries=[Industry(i) for i in excl_ind],
                keywords=[k for k in keywords.split(",") if k.strip()],
                excluded_keywords=[k for k in excl_kw.split(",") if k.strip()],
                excluded_business_types=[BusinessType(x) for x in excl_bt], size_preference=[SizeBand(s) for s in sizes],
                internship_type=internship_type, skills=[s for s in skills.split(",") if s.strip()],
                task_preferences=crit.task_preferences, desired_count=int(desired), min_quality=crit.min_quality,
                require_engineering_team=req_eng, providers=run_providers)
            st.caption("Summary: " + final.summary())
            if st.button("Start search", type="primary", disabled=not final.areas):
                job_id = jobs.create_job(c, ParsedQuery(criteria=final, method=parsed.method, notes=parsed.notes))
                start_job(ui.base_config(), job_id)
                st.session_state.active_job = job_id
                del st.session_state["parsed"]
                st.rerun()

# ------------------------------------------------------------------ import
with tab_import:
    st.markdown("Use imports for sources ATLAS does not collect automatically (member directories, permission-gated "
                "datasets, lists you compiled yourself). Make sure the source's terms allow this use.")
    kind = st.radio("Import type", ["CSV / Excel file", "Website URLs", "Public directory page"], horizontal=True)
    base_crit = SearchCriteria(raw_query="manual import", areas=[AREAS["uae"]], desired_count=50)
    imp_ind = st.multiselect("Industries to assess against", [i.value for i in Industry],
                             default=[i.value for i in Industry if i in (Industry.MECHANICAL, Industry.ROBOTICS,
                                                                         Industry.AEROSPACE, Industry.AI_ML)],
                             format_func=lambda v: INDUSTRY_LABELS[v], key="imp_ind")
    imp_area = st.multiselect("Location to assess against", list(AREAS), default=["dubai"],
                              format_func=lambda k: AREAS[k].label, key="imp_area")

    def run_import(cands, label: str, provider: str):
        crit = base_crit.model_copy(update={"raw_query": f"Import: {label}", "industries": [Industry(i) for i in imp_ind],
                                            "areas": [AREAS[k] for k in imp_area] or [AREAS["uae"]],
                                            "providers": [provider], "desired_count": max(1, len(cands))})
        job_id = jobs.create_job(c, ParsedQuery(criteria=crit, method="manual"))
        jobs.start_provider_run(c, job_id, provider)
        with transaction(c):
            for cand in cands:
                ingest_candidate(c, cand, job_id)
        jobs.finish_provider_run(c, job_id, provider, status="completed", result_count=len(cands), notes=label)
        pdb.record_run(c, provider, ok=True, result_count=len(cands))
        start_job(ui.base_config(), job_id)
        st.session_state.active_job = job_id
        st.success(f"Imported {len(cands)} companies into job #{job_id}; research has started.")

    if kind == "CSV / Excel file":
        up = st.file_uploader("Company list", type=["csv", "xlsx"])
        st.caption("Recognised columns: name/company, website/url, email, phone, address, city/emirate, industry/sector/"
                   "activity, notes. At least a name or website is required.")
        if up is not None:
            try:
                rows = parse_tabular(up.getvalue(), up.name)
                cands, problems = rows_to_candidates(rows, source_label=up.name)
                st.dataframe(pd.DataFrame([{"name": x.name, "website": x.website, "city": x.location.city if x.location else None}
                                           for x in cands[:50]]), hide_index=True, use_container_width=True)
                st.caption(f"{len(cands)} usable rows" + (f"; {len(problems)} skipped" if problems else ""))
                for p in problems[:10]:
                    st.caption("• " + p)
                if cands and st.button("Import and research", type="primary"):
                    run_import(cands, up.name, "manual_csv")
            except ProviderError as exc:
                st.error(str(exc))
    elif kind == "Website URLs":
        txt = st.text_area("One company website per line", height=160, placeholder="https://www.example-engineering.ae")
        if txt.strip():
            cands, problems = urls_to_candidates(txt)
            st.caption(f"{len(cands)} valid company websites" + (f"; {len(problems)} skipped" if problems else ""))
            for p in problems[:10]:
                st.caption("• " + p)
            if cands and st.button("Import and research", type="primary"):
                run_import(cands, f"{len(cands)} URLs", "url_list")
    else:
        url = st.text_input("Directory page URL (one public page listing companies with links to their websites)")
        ok = st.checkbox("I have checked that this directory's terms of use allow collecting this list for personal use.")
        if url and ok and st.button("Fetch links from page"):
            async def _dir():
                async with Fetcher(cfg.http, None) as f:
                    import logging
                    ctx = ProviderContext(cfg=cfg, fetcher=f, log=logging.getLogger("atlas.ui"))
                    return await directory_page_candidates(url, ctx)
            try:
                cands, notes = asyncio.run(_dir())
                st.session_state.dir_cands = [x.model_dump(mode="json") for x in cands]
                for n in notes:
                    st.caption(n)
            except ProviderError as exc:
                st.error(str(exc))
        if st.session_state.get("dir_cands"):
            from atlas.models.company import CandidateCompany
            cands = [CandidateCompany.model_validate(x) for x in st.session_state.dir_cands]
            st.dataframe(pd.DataFrame([{"name": x.name, "website": x.website} for x in cands]), hide_index=True,
                         use_container_width=True)
            if st.button("Import and research these websites", type="primary"):
                run_import(cands, url, "directory_page")
                st.session_state.dir_cands = []

# ------------------------------------------------------------------ progress (live)
@st.fragment(run_every="2s")
def progress_panel():
    job_id = st.session_state.get("active_job")
    recent = jobs.list_jobs(c, 1)
    if job_id is None and recent:
        job_id = recent[0]["id"]
    if job_id is None:
        return
    j = jobs.get_job(c, job_id)
    if j is None:
        return
    with st.container(border=True):
        top = st.columns([4, 1, 1])
        color = {"completed": "green", "running": "blue", "failed": "red", "cancelled": "amber",
                 "interrupted": "amber"}.get(j["status"], "grey")
        top[0].markdown(f"**Job #{j['id']}** {ui.pill(j['status'], color)} — {html.escape(j['query_text'][:90])}",
                        unsafe_allow_html=True)
        # Cancellation goes through the database, so it works for jobs started from the CLI too.
        running = j["status"] == "running"
        if running and top[1].button("Cancel", key=f"cancel_{job_id}"):
            jobs.request_cancel(c, job_id)
        if j["status"] in ("interrupted", "cancelled", "failed") and top[2].button("Resume", key=f"resume_{job_id}"):
            start_job(ui.base_config(), job_id)
        total = j["progress_total"] or 0
        done = j["progress_done"] or 0
        stage = j["stage"] or ""
        if total:
            st.progress(min(1.0, done / total), text=f"{stage}: {done}/{total} — {j['message'] or ''}")
        else:
            st.caption(f"{stage} — {j['message'] or ''}")
        runs = provider_run_summary(c, job_id)
        if runs:
            st.dataframe(pd.DataFrame([{"Source": r["display_name"], "Status": r["status"], "Results": r["result_count"],
                                        "Notes": (r["notes"] or r["error"] or "")[:160]} for r in runs]),
                         hide_index=True, use_container_width=True)
        with st.expander("Activity log", expanded=running):
            for e in jobs.list_events(c, job_id, 40):
                st.markdown(f"<span class='muted'>{e['ts'][11:19]}</span> {ui.pill(e['level'], 'red' if e['level']=='error' else 'amber' if e['level']=='warning' else 'grey')} {html.escape(e['message'])}",
                            unsafe_allow_html=True)
        if j["status"] == "completed":
            if st.button("View results in Prospect Explorer", key=f"view_{job_id}"):
                st.session_state.explorer_job = job_id
                st.switch_page("views/explorer.py")


with tab_search:
    st.markdown("##### Current / latest job")
    progress_panel()

# ------------------------------------------------------------------ history
with tab_jobs:
    hist = jobs.list_jobs(c, 50)
    if not hist:
        ui.empty_state("No searches yet.")
    for j in hist:
        with st.expander(f"#{j['id']} · {j['status']} · {j['query_text'][:80]}"):
            st.caption(f"Created {j['created_at']} · finished {j['finished_at'] or '—'}")
            st.write(j["message"] or "")
            cols = st.columns(3)
            if cols[0].button("Show progress", key=f"show_{j['id']}"):
                st.session_state.active_job = j["id"]
                st.rerun()
            if cols[1].button("Open results", key=f"res_{j['id']}"):
                st.session_state.explorer_job = j["id"]
                st.switch_page("views/explorer.py")
            if j["status"] in ("interrupted", "cancelled", "failed") and cols[2].button("Resume", key=f"r_{j['id']}"):
                start_job(ui.base_config(), j["id"])
                st.session_state.active_job = j["id"]
                st.rerun()

# ------------------------------------------------------------------ sources
with tab_sources:
    st.markdown("Every source ATLAS knows about, including those **deliberately not automated** and why.")
    rows = pdb.list_providers(c)
    st.dataframe(pd.DataFrame([{
        "Source": p["display_name"], "Availability": p["availability"].replace("_", " "),
        "Enabled": bool(p["enabled"]), "Last success": (p["last_success_at"] or "never")[:16],
        "Last results": p["last_result_count"], "Last error": (p["last_error"] or "")[:120],
        "Health": (p["last_health"] or "")[:120], "Coverage": p["coverage"], "Limitations": p["limitations"],
        "URL": p["source_url"]} for p in rows]), hide_index=True, use_container_width=True,
        column_config={"URL": st.column_config.LinkColumn()})
    st.caption("Enable/disable sources and run health checks in Settings → Discovery.")
