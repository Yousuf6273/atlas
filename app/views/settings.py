from __future__ import annotations

import asyncio
import html
import logging

import pandas as pd
import streamlit as st
import ui

from atlas.config import ScoringWeights
from atlas.crawl.render import playwright_available
from atlas.db import providers as pdb
from atlas.db.settings import (
    get_setting,
    save_config_section,
    save_profile,
    set_setting,
)
from atlas.export.exporter import export_rows, to_csv_bytes, to_xlsx_bytes
from atlas.llm.client import OllamaClient
from atlas.models.profile import ProjectEntry, SkillEntry, StudentProfile
from atlas.net.cache import HttpCache
from atlas.net.fetcher import Fetcher
from atlas.pipeline.runner import rescore_all
from atlas.providers import registry
from atlas.providers.base import ProviderContext

ui.setup_page("Settings", "Scoring, your profile, crawling limits, data sources, local AI and data export.")
c = ui.conn()
cfg = ui.cfg()

t_w, t_p, t_c, t_d, t_ai, t_data = st.tabs(["Scoring weights", "Profile", "Crawler", "Discovery", "Local AI", "Data & export"])

with t_w:
    st.markdown("Weights are relative; they are normalised to 100 %. Changing them re-ranks existing companies "
                "without re-crawling.")
    w = cfg.scoring
    cols = st.columns(3)
    vals = {}
    for i, (key, label) in enumerate([("industry", "Industry & technical relevance"), ("tasks", "Useful student tasks"),
                                      ("internship", "Internship accessibility"), ("contact", "Contact quality"),
                                      ("geography", "Geographic suitability"), ("activity", "Evidence of activity")]):
        vals[key] = cols[i % 3].slider(label, 0, 50, int(getattr(w, key)), key=f"w_{key}")
    norm = ScoringWeights(**vals).normalized()
    st.caption(" · ".join(f"{k} {v:.0%}" for k, v in norm.items()))
    a, b = st.columns(2)
    if a.button("Save and re-rank", type="primary"):
        save_config_section(c, "scoring", vals)
        n = rescore_all(ui.base_config())
        st.success(f"Saved. Re-ranked {n} companies.")
    if b.button("Reset to defaults (25/25/20/15/10/5)"):
        save_config_section(c, "scoring", ScoringWeights().model_dump())
        rescore_all(ui.base_config())
        st.rerun()

with t_p:
    prof = ui.profile()
    st.markdown("Drafts use **only** what is written here. Leave a field blank rather than guess.")
    with st.form("profile"):
        a, b, d = st.columns(3)
        full_name = a.text_input("Full name", prof.full_name)
        age = b.number_input("Age", 13, 25, prof.age or 17)
        school = d.text_input("School", prof.school)
        a, b = st.columns(2)
        email = a.text_input("Your email", prof.email)
        phone = b.text_input("Phone (optional)", prof.phone)
        subjects = st.text_input("Subjects (comma-separated)", ", ".join(prof.subjects))
        a, b = st.columns(2)
        city = a.text_input("City", prof.location_city)
        summer = b.number_input("Summer year", 2025, 2035, prof.summer_year)
        availability = st.text_input("Availability", prof.availability)
        placement = st.text_input("Preferred placement", prof.preferred_placement)
        interests = st.text_input("Interests", ", ".join(prof.interests))
        a, b, d = st.columns(3)
        linkedin = a.text_input("LinkedIn URL", prof.linkedin_url)
        github = b.text_input("GitHub URL", prof.github_url)
        portfolio = d.text_input("Portfolio URL", prof.portfolio_url)
        st.markdown("**Skills** — level and a concrete example of something you have actually done (optional).")
        skills_df = st.data_editor(pd.DataFrame([s.model_dump() for s in prof.skills] or [{"name": "", "level": "unspecified",
                                                                                           "evidence": ""}]),
                                   num_rows="dynamic", use_container_width=True, key="skills_ed",
                                   column_config={"level": st.column_config.SelectboxColumn(
                                       options=["unspecified", "learning", "working knowledge", "confident"])})
        st.markdown("**Projects** — real projects only; the first one may be mentioned in detailed emails.")
        proj_df = st.data_editor(pd.DataFrame([p.model_dump() for p in prof.projects] or
                                              [{"title": "", "description": "", "url": ""}]),
                                 num_rows="dynamic", use_container_width=True, key="proj_ed")
        cv_note = st.checkbox("Mention an attached CV in emails", prof.cv_attached_note)
        cv_summary = st.text_area("CV summary (for your reference)", prof.cv_summary, height=80)
        if st.form_submit_button("Save profile", type="primary"):
            new = StudentProfile(
                full_name=full_name.strip(), age=int(age), school=school.strip(), email=email.strip(), phone=phone.strip(),
                subjects=[s.strip() for s in subjects.split(",") if s.strip()], location_city=city.strip() or "Dubai",
                summer_year=int(summer), availability=availability, preferred_placement=placement,
                interests=[s.strip() for s in interests.split(",") if s.strip()],
                linkedin_url=linkedin.strip(), github_url=github.strip(), portfolio_url=portfolio.strip(),
                skills=[SkillEntry(name=str(r["name"]).strip(), level=r.get("level") or "unspecified",
                                   evidence=str(r.get("evidence") or "")) for r in skills_df.to_dict("records")
                        if str(r.get("name") or "").strip()],
                projects=[ProjectEntry(title=str(r["title"]).strip(), description=str(r.get("description") or ""),
                                       url=(str(r.get("url")).strip() or None) if r.get("url") else None)
                          for r in proj_df.to_dict("records") if str(r.get("title") or "").strip()],
                cv_attached_note=cv_note, cv_summary=cv_summary)
            save_profile(c, new)
            st.success("Profile saved. Use 'Regenerate drafts' in Outreach Studio to apply it.")
    default_area = get_setting(c, "default_area", "dubai")
    from atlas.geo.areas import AREAS

    da = st.selectbox("Default search location (used when a query names none)", list(AREAS),
                      index=list(AREAS).index(default_area), format_func=lambda k: AREAS[k].label)
    if da != default_area:
        set_setting(c, "default_area", da)

with t_c:
    with st.form("crawl"):
        cc = cfg.crawl
        a, b, d = st.columns(3)
        pages = a.number_input("Max pages per website", 1, 40, cc.max_pages_per_site)
        depth = b.number_input("Max link depth", 0, 4, cc.max_depth)
        conc = d.number_input("Companies researched in parallel", 1, 16, cc.company_concurrency)
        a, b, d = st.columns(3)
        cap = a.number_input("Max companies researched per search", 1, 1000, cc.max_companies_to_research)
        mult = b.number_input("Research multiplier (× target)", 1.0, 10.0, float(cc.research_multiplier), step=0.5)
        refresh = d.number_input("Re-use research younger than (days)", 1, 365, cc.page_refresh_days)
        a, b = st.columns(2)
        interval = a.number_input("Seconds between requests to the same site", 0.5, 30.0,
                                  float(cfg.http.per_domain_min_interval_s), step=0.5)
        ttl = b.number_input("HTTP cache lifetime (hours)", 1.0, 2000.0, float(cfg.http.cache_ttl_hours))
        sitemaps = st.checkbox("Use sitemaps to find key pages", cc.use_sitemaps)
        pw = st.checkbox("Use Playwright for JavaScript-only sites", cc.enable_playwright,
                         help="Requires `pip install playwright && playwright install chromium`.",
                         disabled=not playwright_available())
        if not playwright_available():
            st.caption("Playwright is not installed — JavaScript-only sites are recorded as 'content may be incomplete'.")
        st.caption("robots.txt is always respected and cannot be switched off.")
        if st.form_submit_button("Save crawler settings"):
            save_config_section(c, "crawl", {"max_pages_per_site": int(pages), "max_depth": int(depth),
                                             "company_concurrency": int(conc), "max_companies_to_research": int(cap),
                                             "research_multiplier": float(mult), "page_refresh_days": int(refresh),
                                             "use_sitemaps": sitemaps, "enable_playwright": pw})
            save_config_section(c, "http", {"per_domain_min_interval_s": float(interval), "cache_ttl_hours": float(ttl)})
            st.success("Saved.")

with t_d:
    st.markdown("##### Sources")
    for p in pdb.list_providers(c):
        impl = registry.get_provider(p["name"])
        cols = st.columns([3, 1, 4])
        cols[0].markdown(f"**{html.escape(p['display_name'])}**  \n<span class='muted'>{p['availability'].replace('_', ' ')}</span>",
                         unsafe_allow_html=True)
        if impl is not None and impl.info.runs_in_search:
            en = cols[1].toggle("Enabled", bool(p["enabled"]), key=f"en_{p['name']}")
            if en != bool(p["enabled"]):
                pdb.set_enabled(c, p["name"], en)
        else:
            cols[1].caption("manual / n.a.")
        cols[2].caption(p["limitations"] or "")
    if st.button("Run health checks"):
        async def _hc():
            out = []
            async with Fetcher(cfg.http, HttpCache(cfg.paths.cache_db_path)) as f:
                ctx = ProviderContext(cfg=cfg, fetcher=f, log=logging.getLogger("atlas.ui"))
                for prov in registry.IMPLEMENTED:
                    if prov.info.runs_in_search:
                        ok, detail = await prov.health_check(ctx)
                        pdb.record_health(c, prov.name, ("OK: " if ok else "FAIL: ") + detail)
                        out.append((prov.info.display_name, ok, detail))
            return out
        for name, ok, detail in asyncio.run(_hc()):
            (st.success if ok else st.warning)(f"{name}: {detail}")
    with st.form("disc"):
        dd = cfg.discovery
        endpoints = st.text_area("Overpass endpoints (one per line, tried in turn)", "\n".join(dd.overpass_endpoints))
        a, b = st.columns(2)
        res = a.checkbox("Try to find websites for companies without one (verified name-derived domains)",
                         dd.website_resolution)
        res_max = b.number_input("Max website look-ups per search", 0, 500, dd.website_resolution_max)
        searx = st.text_input("Self-hosted SearXNG URL (optional)", dd.searxng_url or "", placeholder="http://localhost:8888")
        if st.form_submit_button("Save discovery settings"):
            save_config_section(c, "discovery", {"overpass_endpoints": [e.strip() for e in endpoints.splitlines() if e.strip()],
                                                 "website_resolution": res, "website_resolution_max": int(res_max),
                                                 "searxng_url": searx.strip() or None})
            st.success("Saved.")

with t_ai:
    st.markdown("Optional. With [Ollama](https://ollama.com) running locally, ATLAS can suggest filters for vague queries "
                "and summarise company activities (every AI statement must quote the website verbatim or it is "
                "discarded). Everything works without it.")
    with st.form("llm"):
        l = cfg.llm
        enabled = st.checkbox("Use local AI when available", l.enabled)
        a, b = st.columns(2)
        base = a.text_input("Ollama URL", l.base_url)
        model = b.text_input("Model", l.model)
        a, b, d = st.columns(3)
        uq = a.checkbox("Query interpretation", l.use_for_query_parsing)
        us = b.checkbox("Company activity summaries", l.use_for_company_summary)
        up = d.checkbox("Draft polishing (guarded)", l.use_for_draft_polish)
        if st.form_submit_button("Save"):
            save_config_section(c, "llm", {"enabled": enabled, "base_url": base, "model": model,
                                           "use_for_query_parsing": uq, "use_for_company_summary": us,
                                           "use_for_draft_polish": up})
            st.success("Saved.")
    if st.button("Test connection"):
        ok, why = asyncio.run(OllamaClient(ui.cfg().llm, c).check())
        (st.success if ok else st.warning)(why)

with t_data:
    st.markdown("##### Export everything")
    df = export_rows(c)
    a, b = st.columns(2)
    a.download_button("All companies (XLSX)", to_xlsx_bytes(df), "atlas_all.xlsx")
    b.download_button("All companies (CSV)", to_csv_bytes(df), "atlas_all.csv")
    st.markdown("##### Storage")
    st.caption(f"Database: {cfg.paths.db_path}")
    st.caption(f"Logs: {cfg.paths.log_dir / 'atlas.jsonl'}")
    cache = HttpCache(cfg.paths.cache_db_path)
    stats = cache.stats()
    st.caption(f"HTTP cache: {stats['entries']} responses, {stats['compressed_bytes'] / 1e6:.1f} MB compressed")
    if st.button("Clear expired cache entries"):
        st.success(f"Removed {cache.purge_expired()} entries")
    cache.close()
