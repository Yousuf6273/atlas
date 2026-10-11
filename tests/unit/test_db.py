"""Database layer: migrations, constraints, provenance and history retention."""

from __future__ import annotations

import sqlite3

import pytest

from atlas.db import (
    assessments,
    companies,
    contacts,
    jobs,
    outreach,
    research,
    review,
    settings,
)
from atlas.db.core import migrate, schema_version
from atlas.models import (
    CandidateCompany,
    CandidateLocation,
    Claim,
    ContactCandidate,
    Evidence,
    ParsedQuery,
    QualificationResult,
    SearchCriteria,
)
from atlas.models.enums import ContactChannel, Industry, JobStatus, QualificationStatus


def test_migrations_idempotent(conn):
    assert schema_version(conn) >= 1
    assert migrate(conn) == []  # nothing pending on second run
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {
        "search_jobs", "search_filters", "discovery_providers", "companies", "company_locations", "company_sources",
        "webpages", "extracted_claims", "qualification_assessments", "internship_assessments", "task_opportunities",
        "contacts", "contact_evidence", "verification_results", "prospect_scores", "outreach_drafts",
        "outreach_history", "crawl_jobs", "review_queue", "app_settings", "field_history", "do_not_contact",
    }
    assert required <= tables


def test_job_lifecycle_and_filters(conn):
    crit = SearchCriteria(raw_query="robotics in Dubai", industries=[Industry.ROBOTICS], desired_count=10)
    job_id = jobs.create_job(conn, ParsedQuery(criteria=crit))
    filters = conn.execute("SELECT filter_key, filter_value FROM search_filters WHERE job_id = ?", (job_id,)).fetchall()
    assert ("industry", "robotics") in [tuple(r) for r in filters]
    jobs.set_status(conn, job_id, JobStatus.RUNNING)
    jobs.update_progress(conn, job_id, stage="discovery", done=1, total=4)
    j = jobs.get_job(conn, job_id)
    assert j["status"] == "running" and j["progress_total"] == 4 and j["started_at"]
    jobs.request_cancel(conn, job_id)
    assert jobs.cancel_requested(conn, job_id)
    assert jobs.job_criteria(conn, job_id).industries == [Industry.ROBOTICS]


def test_stale_running_job_marked_interrupted(conn):
    job_id = jobs.create_job(conn, ParsedQuery(criteria=SearchCriteria(raw_query="x")))
    jobs.set_status(conn, job_id, JobStatus.RUNNING)
    conn.execute("UPDATE search_jobs SET heartbeat_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (job_id,))
    assert jobs.mark_stale_jobs_interrupted(conn) == [job_id]
    assert jobs.get_job(conn, job_id)["status"] == "interrupted"


def test_status_check_constraint(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO search_jobs(query_text, criteria_json, parser_method, status, created_at) "
                     "VALUES ('q', '{}', 'x', 'bogus', '2026-01-01')")


def test_set_field_keeps_higher_confidence_and_history(conn):
    cid = companies.create_company(conn, name="Synthetic Robotics Testco", normalized_name="synthetic robotics testco",
                                   website="https://synthetic-robotics.example", domain="synthetic-robotics.example",
                                   website_source="official_site", website_confidence=0.9)
    # Lower-confidence value must not overwrite, but must be recorded.
    changed = companies.set_field(conn, cid, "website", "https://other.example", confidence=0.5, source="osm")
    assert changed is False
    assert companies.get_company(conn, cid)["website"] == "https://synthetic-robotics.example"
    hist = companies.field_history(conn, cid)
    assert any(h["action"] == "kept_existing" and h["new_value"] == "https://other.example" for h in hist)
    # Higher confidence overwrites and keeps the old value in history.
    assert companies.set_field(conn, cid, "website", "https://new.example", confidence=1.0, source="user")
    hist = companies.field_history(conn, cid)
    assert any(h["action"] == "overwritten" and h["old_value"] == "https://synthetic-robotics.example" for h in hist)


def test_source_provenance_and_merge(conn):
    a = companies.create_company(conn, name="Synthetic Aero Testco LLC", normalized_name="synthetic aero testco")
    b = companies.create_company(conn, name="Synthetic Aero Testco", normalized_name="synthetic aero testco",
                                 website="https://synthetic-aero.example", domain="synthetic-aero.example")
    ca = CandidateCompany(name="Synthetic Aero Testco LLC", provider="osm", external_id="node/1",
                          location=CandidateLocation(city="Dubai", latitude=25.1, longitude=55.2))
    cb = CandidateCompany(name="Synthetic Aero Testco", provider="wikidata", external_id="Q0")
    companies.add_source(conn, a, ca, None)
    companies.add_location(conn, a, ca.location, provider="osm", source_url=None)
    companies.add_source(conn, b, cb, None)
    companies.merge_companies(conn, b, a, reason="test")
    assert companies.resolve_id(conn, a) == b
    providers = {s["provider"] for s in companies.company_sources(conn, b)}
    assert providers == {"osm", "wikidata"}  # provenance from both records preserved
    assert len(companies.company_locations(conn, b)) == 1


def test_claim_upsert_unique_and_retire(conn):
    cid = companies.create_company(conn, name="Synthetic Co", normalized_name="synthetic co")
    ev = Evidence(source_url="https://synthetic.example/about", snippet="We design robots.")
    c = Claim(claim_type="activity", value="We design robots", evidence=ev, extractor="rule:activity")
    research.save_claims(conn, cid, [c, c])
    assert len(research.company_claims(conn, cid)) == 1
    research.retire_claims(conn, cid)
    assert research.company_claims(conn, cid) == []
    assert len(research.company_claims(conn, cid, current_only=False)) == 1


def test_assessment_versioning(conn):
    cid = companies.create_company(conn, name="Synthetic Co", normalized_name="synthetic co")
    q1 = QualificationResult(status=QualificationStatus.NEEDS_REVIEW, target_relevance=30, primary_industry=None)
    q2 = QualificationResult(status=QualificationStatus.QUALIFIED, target_relevance=80, primary_industry="robotics")
    assessments.save_qualification(conn, cid, None, q1)
    assessments.save_qualification(conn, cid, None, q2)
    assert assessments.current_qualification(conn, cid).status == QualificationStatus.QUALIFIED
    n = conn.execute("SELECT COUNT(*) FROM qualification_assessments WHERE company_id = ?", (cid,)).fetchone()[0]
    assert n == 2  # history retained


def test_contact_upsert_and_evidence(conn):
    cid = companies.create_company(conn, name="Synthetic Co", normalized_name="synthetic co")
    cc = ContactCandidate(channel=ContactChannel.EMAIL, value="Info@Synthetic.example", source_type="osm")
    id1 = contacts.upsert_contact(conn, cid, cc)
    cc2 = cc.model_copy(update={"source_type": "official_site", "source_url": "https://synthetic.example/contact",
                                "snippet": "Email: info@synthetic.example"})
    id2 = contacts.upsert_contact(conn, cid, cc2)
    assert id1 == id2
    rows = contacts.company_contacts(conn, cid)
    assert rows[0]["source_type"] == "official_site"
    assert rows[0]["evidence"][0]["source_url"] == "https://synthetic.example/contact"


def test_dnc_and_history(conn):
    cid = companies.create_company(conn, name="Synthetic Co", normalized_name="synthetic co")
    outreach.add_dnc(conn, domain="synthetic.example", reason="asked not to be contacted")
    assert outreach.is_dnc(conn, email="hr@synthetic.example") == "asked not to be contacted"
    assert outreach.is_dnc(conn, company_id=cid) is None
    outreach.add_history(conn, cid, status="contacted", contact_value="hr@synthetic.example")
    assert outreach.latest_status(conn, cid)["status"] == "contacted"
    assert outreach.sent_contact_values(conn) == {"hr@synthetic.example": cid}


def test_approved_draft_not_overwritten(conn):
    cid = companies.create_company(conn, name="Synthetic Co", normalized_name="synthetic co")
    d1 = outreach.save_draft(conn, cid, kind="short_email", subject="s", body="b1", facts=[], method="template")
    outreach.set_draft_approved(conn, d1, True)
    d2 = outreach.save_draft(conn, cid, kind="short_email", subject="s", body="b2", facts=[], method="template")
    assert d1 != d2
    assert outreach.get_draft(conn, d1)["body"] == "b1"


def test_review_queue_unique_open(conn):
    a = companies.create_company(conn, name="A", normalized_name="a")
    b = companies.create_company(conn, name="B", normalized_name="b")
    review.enqueue(conn, item_type="possible_duplicate", company_id=a, other_company_id=b, reason="r1", score=0.8)
    review.enqueue(conn, item_type="possible_duplicate", company_id=a, other_company_id=b, reason="r2", score=0.9)
    items = review.open_items(conn)
    assert len(items) == 1 and items[0]["reason"] == "r2"
    review.enqueue(conn, item_type="qualification", company_id=a, reason="borderline")
    assert review.count_open(conn) == 2


def test_settings_overlay(conn, cfg):
    settings.save_config_section(conn, "scoring", {"industry": 50})
    eff = settings.effective_config(conn, cfg)
    assert eff.scoring.industry == 50 and eff.scoring.tasks == 25
    with pytest.raises(ValueError):
        settings.save_config_section(conn, "paths", {})
    prof = settings.load_profile(conn)
    assert prof.age == 17 and "Physics" in prof.subjects


def test_partial_provider_runs_are_rerun_on_resume(conn):
    job_id = jobs.create_job(conn, ParsedQuery(criteria=SearchCriteria(raw_query="x")))
    for prov in ("osm_overpass", "wikidata"):
        conn.execute("INSERT OR IGNORE INTO discovery_providers(name, display_name, availability, updated_at) "
                     "VALUES (?, ?, 'available', '2026-01-01')", (prov, prov))
        jobs.start_provider_run(conn, job_id, prov)
    jobs.finish_provider_run(conn, job_id, "osm_overpass", status="completed", partial=True)
    jobs.finish_provider_run(conn, job_id, "wikidata", status="completed")
    assert jobs.completed_providers(conn, job_id) == {"wikidata"}
    assert schema_version(conn) >= 2


def test_qualification_and_scores_are_per_search(conn):
    cid = companies.create_company(conn, name="Synthetic Co", normalized_name="synthetic co")
    j1 = jobs.create_job(conn, ParsedQuery(criteria=SearchCriteria(raw_query="software")))
    j2 = jobs.create_job(conn, ParsedQuery(criteria=SearchCriteria(raw_query="aerospace")))
    assessments.save_qualification(conn, cid, j1, QualificationResult(
        status=QualificationStatus.QUALIFIED, target_relevance=80, primary_industry="software"))
    assessments.save_qualification(conn, cid, j2, QualificationResult(
        status=QualificationStatus.NEEDS_REVIEW, target_relevance=20, primary_industry="software"))
    assert assessments.current_qualification(conn, cid, j1).status == QualificationStatus.QUALIFIED
    assert assessments.current_qualification(conn, cid, j2).status == QualificationStatus.NEEDS_REVIEW
    assert assessments.current_qualification(conn, cid).status == QualificationStatus.NEEDS_REVIEW  # latest
    assert set(assessments.current_assessment_jobs(conn, cid)) == {j1, j2}
