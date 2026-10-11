"""End-to-end pipeline run against SYNTHETIC websites and mocked provider APIs.

No network access: Overpass, Wikidata, GLEIF and all company sites are served
by respx; DNS is stubbed. Verifies discovery → dedup → crawl → extraction →
qualification → internship → tasks → contacts → scoring → drafts → export.
"""

from __future__ import annotations

import io

import httpx
import pandas as pd
import pytest
import respx

from atlas.contacts.verify import DomainMailChecker
from atlas.db import assessments, companies, contacts, jobs, outreach, research
from atlas.db.core import open_db
from atlas.db.queries import overview_counts, prospect_rows
from atlas.export.exporter import export_rows, to_xlsx_bytes
from atlas.geo.areas import AREAS
from atlas.models.enums import Industry
from atlas.models.search import ParsedQuery, SearchCriteria
from atlas.pipeline import identity
from atlas.pipeline.orchestrator import run_job
from tests import synthetic_web

OSM = {"elements": [
    {"type": "node", "id": 9001, "lat": 25.1217, "lon": 55.3855,
     "tags": {"name": "Synthetic Robotics Testco", "office": "company", "website": "https://synthetic-robotics.example",
              "phone": "+971 4 321 0001"}},
    {"type": "node", "id": 9002, "lat": 24.9144, "lon": 55.1615,
     "tags": {"name": "Synthetic Aero Services", "office": "company", "website": "https://synthetic-aero.example"}},
    {"type": "node", "id": 9003, "lat": 25.13, "lon": 55.23,
     "tags": {"name": "Synthetic Rent A Car Engineering", "office": "company",
              "website": "https://synthetic-rentals.example"}},
    {"type": "node", "id": 9004, "lat": 25.13, "lon": 55.23,
     "tags": {"name": "Synthetic Parked Robotics", "office": "company", "website": "https://synthetic-parked.example"}},
    {"type": "node", "id": 9005, "lat": 25.13, "lon": 55.24,
     "tags": {"name": "Synthetic Blocked Automation", "office": "company", "website": "https://synthetic-blocked.example"}},
    {"type": "node", "id": 9006, "lat": 25.1313, "lon": 55.2337,
     "tags": {"name": "Synthetic Pumps Trading", "craft": "metal_construction",
              "website": "https://synthetic-trading.example"}},
]}
WIKIDATA = {"results": {"bindings": [
    # Same company as OSM node 9001 (same domain) -> must merge, keeping both sources.
    {"org": {"value": "http://www.wikidata.org/entity/Q999000001"}, "orgLabel": {"value": "Synthetic Robotics Testco FZ-LLC"},
     "orgDescription": {"value": "industrial robotics company"}, "website": {"value": "https://synthetic-robotics.example/"},
     "typeLabel": {"value": "company"}, "industryLabel": {"value": "robotics"}, "hqLabel": {"value": "Dubai Silicon Oasis"}},
]}}


@pytest.fixture
def stub_dns(monkeypatch):
    async def fake_lookup(self, domain):
        if domain.endswith(".example"):
            return True, "MX: mx.synthetic.example (stubbed in tests)"
        return False, "NXDOMAIN (stub)"

    async def no_resolve(domain, timeout=4.0):
        return False

    monkeypatch.setattr(DomainMailChecker, "_lookup", fake_lookup)
    monkeypatch.setattr(identity, "_resolves", no_resolve)


@pytest.fixture
def mocked_web(cfg):
    cfg.discovery.overpass_endpoints = ["https://overpass.test/api/interpreter"]
    cfg.llm.enabled = False
    cfg.crawl.company_concurrency = 3
    with respx.mock(assert_all_called=False) as router:
        router.post("https://overpass.test/api/interpreter").mock(return_value=httpx.Response(200, json=OSM))
        router.get(url__startswith="https://query.wikidata.org/sparql").mock(return_value=httpx.Response(
            200, json=WIKIDATA, headers={"content-type": "application/sparql-results+json"}))
        router.get(url__startswith="https://api.gleif.org/").mock(return_value=httpx.Response(
            200, json={"data": []}, headers={"content-type": "application/vnd.api+json"}))
        synthetic_web.mount(router)
        yield router


def _job(conn, **kw) -> int:
    crit = SearchCriteria(raw_query="synthetic test search", industries=[Industry.ROBOTICS, Industry.MECHANICAL,
                                                                         Industry.AEROSPACE, Industry.AUTOMOTIVE],
                          areas=[AREAS["dubai"]], desired_count=10, **kw)
    return jobs.create_job(conn, ParsedQuery(criteria=crit))


def _by_name(conn, needle):
    row = conn.execute("SELECT id FROM companies WHERE name LIKE ? AND merged_into_id IS NULL", (f"%{needle}%",)).fetchone()
    assert row, f"company {needle!r} not found"
    return int(row[0])


async def test_full_pipeline_on_synthetic_web(cfg, mocked_web, stub_dns):
    conn = open_db(cfg.paths.db_path)
    job_id = _job(conn)
    status = await run_job(cfg, job_id)
    conn = open_db(cfg.paths.db_path)
    job = jobs.get_job(conn, job_id)
    assert status == "completed", job["error"]

    # --- dedup + provenance: OSM + Wikidata records of the robotics firm merged
    rob = _by_name(conn, "Synthetic Robotics")
    provs = {s["provider"] for s in companies.company_sources(conn, rob)}
    assert provs == {"osm_overpass", "wikidata"}
    assert conn.execute("SELECT COUNT(*) FROM companies WHERE name LIKE 'Synthetic Robotics%' AND merged_into_id IS NULL"
                        ).fetchone()[0] == 1

    # --- robotics firm: qualified, confirmed opportunity, identity verified, rich evidence
    q = assessments.current_qualification(conn, rob)
    assert q.status.value == "qualified", q.reasons
    assert q.geo_status.value == "in_area"
    assert q.size_band.value == "small"  # "around 40 employees"
    a = assessments.current_internship(conn, rob)
    assert a.category.value == "confirmed_opportunity" and a.school_students_welcome
    assert companies.get_company(conn, rob)["identity_status"] == "verified"
    tasks = assessments.current_tasks(conn, rob)
    keys = {t.template_key for t in tasks}
    assert keys & {"bom_organisation", "cad_support", "qc_reporting", "robotics_test_logging", "supplier_research"}
    assert any(t.basis.value == "observed_need" for t in tasks)  # Document Controller / Data Analyst vacancies
    for t in tasks:
        assert t.evidence and all(e.source_url.startswith("https://synthetic-robotics.example") for e in t.evidence)
    cts = contacts.company_contacts(conn, rob)
    emails = {c["value"]: c for c in cts if c["channel"] == "email"}
    assert set(emails) == {"info@synthetic-robotics.example", "careers@synthetic-robotics.example"}
    assert emails["careers@synthetic-robotics.example"]["verification_status"] == "publicly_published"
    assert not any("jane" in v or "omar" in v for v in emails)  # no guessed personal emails
    # robots.txt-disallowed and binary links were never fetched
    urls = {p["url"] for p in research.company_pages(conn, rob, with_text=False)}
    assert not any("/private" in u or u.endswith(".pdf") for u in urls)

    # --- aero: explicitly ineligible (university-only + 18+)
    aero = _by_name(conn, "Synthetic Aero")
    assert assessments.current_internship(conn, aero).category.value == "explicitly_ineligible"

    # --- car rental look-alike: rejected
    rent = _by_name(conn, "Synthetic Rent")
    assert assessments.current_qualification(conn, rent).status.value == "rejected"

    # --- parked domain: rejected as inactive
    parked = _by_name(conn, "Synthetic Parked")
    pq = assessments.current_qualification(conn, parked)
    assert pq.status.value == "rejected" and pq.operational_status.value == "likely_inactive"

    # --- robots-blocked site: nothing fetched, not rejected, sent to review
    blocked = _by_name(conn, "Synthetic Blocked")
    assert research.company_pages(conn, blocked) == [] or all(
        p["status_code"] is None for p in research.company_pages(conn, blocked))
    assert assessments.current_qualification(conn, blocked).status.value == "needs_review"

    # --- distributor: recognised as distributor; prompt-injection text had no effect
    trading = _by_name(conn, "Synthetic Pumps")
    tq = assessments.current_qualification(conn, trading)
    assert tq.business_type.value == "distributor_trading"
    assert tq.size_band.value == "unknown"  # "10,000 employees" appears only in an injection sentence…
    ta = assessments.current_internship(conn, trading)
    assert ta.category.value != "confirmed_opportunity"

    # --- ranking: robotics firm ranks first
    rows = prospect_rows(conn, job_id=job_id)
    assert rows[0]["company_id"] == rob
    assert rows[0]["readiness_label"].startswith("Ready")

    # --- drafts exist, cite facts with sources, and contain no invented numbers
    drafts = outreach.company_drafts(conn, rob)
    kinds = {d["kind"] for d in drafts}
    assert kinds == {"short_email", "detailed_email", "networking_message", "follow_up", "introduction_request"}
    short = next(d for d in drafts if d["kind"] == "short_email")
    assert "Synthetic Robotics Testco" in short["body"] or "robotic" in short["body"].lower()
    assert "[Your full name]" in short["body"]  # profile not filled in → visible placeholder, not invention
    assert any(f.get("source_url", "") and f["source_url"].startswith("https://synthetic-robotics.example")
               for f in short["facts"])
    net = next(d for d in drafts if d["kind"] == "networking_message")
    assert len(net["body"]) <= 300
    assert not outreach.company_drafts(conn, rent)  # no drafts for rejected companies

    # --- job bookkeeping
    stats = jobs.job_stats(conn, job_id)
    assert stats["discovery"]["providers"]["osm_overpass"]["status"] == "completed"
    counts = overview_counts(conn)
    assert counts["qualified"] >= 1 and counts["discovered"] == 6

    # --- export: formula injection escaped, numbers kept numeric
    conn.execute("UPDATE companies SET name = '=HYPERLINK(\"http://evil.example\")' WHERE id = ?", (trading,))
    df = export_rows(conn, job_id=job_id)
    assert df.loc[df["ATLAS id"] == trading, "Company"].iloc[0].startswith("'=")
    xl = pd.read_excel(io.BytesIO(to_xlsx_bytes(df)))
    assert len(xl) == len(df)
    assert pd.api.types.is_numeric_dtype(df["Fit score"])


async def test_resume_skips_completed_work(cfg, mocked_web, stub_dns):
    conn = open_db(cfg.paths.db_path)
    job_id = _job(conn)
    assert await run_job(cfg, job_id) == "completed"
    overpass_calls = mocked_web.routes[0].call_count
    # Simulate an interruption and resume: providers and crawls must not repeat.
    conn = open_db(cfg.paths.db_path)
    conn.execute("UPDATE search_jobs SET status = 'interrupted' WHERE id = ?", (job_id,))
    assert await run_job(cfg, job_id) == "completed"
    assert mocked_web.routes[0].call_count == overpass_calls
    conn = open_db(cfg.paths.db_path)
    events = [e["message"] for e in jobs.list_events(conn, job_id)]
    assert any("already completed for this job" in m for m in events)


async def test_cancellation_is_clean_and_resumable(cfg, mocked_web, stub_dns, monkeypatch):
    conn = open_db(cfg.paths.db_path)
    job_id = _job(conn)
    jobs.request_cancel(conn, job_id)
    # set_status(RUNNING) clears the flag, so request cancel again right after start via a hook:
    import atlas.pipeline.orchestrator as orch

    original = orch.stage_discovery

    async def cancel_then_discover(ctx, criteria):
        jobs.request_cancel(ctx.conn, ctx.job_id)
        return await original(ctx, criteria)

    monkeypatch.setattr(orch, "stage_discovery", cancel_then_discover)
    assert await run_job(cfg, job_id) == "cancelled"
    conn = open_db(cfg.paths.db_path)
    assert jobs.get_job(conn, job_id)["status"] == "cancelled"
    monkeypatch.setattr(orch, "stage_discovery", original)
    assert await run_job(cfg, job_id) == "completed"
