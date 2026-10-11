"""Qualification, internship, tasks, contact verification/quality, scoring, outreach, CRM (SYNTHETIC data)."""

from __future__ import annotations

import pytest

from atlas.assess.dossier import Dossier, PageDoc
from atlas.assess.internship import assess_internship
from atlas.assess.qualification import industry_scores, qualify, size_band_for
from atlas.assess.tasks import TEMPLATES, find_tasks
from atlas.config import QualificationSettings, ScoringWeights
from atlas.contacts.quality import best_contact, channel_quality
from atlas.contacts.verify import DomainMailChecker, verify_email
from atlas.db import companies, outreach
from atlas.geo.areas import AREAS
from atlas.llm.guard import introduces_new_facts
from atlas.models.enums import (
    EmailStatus,
    GeoStatus,
    Industry,
    InternshipCategory,
    OutreachStatus,
    QualificationStatus,
    SizeBand,
    TaskBasis,
)
from atlas.models.profile import SkillEntry, StudentProfile
from atlas.models.search import SearchCriteria
from atlas.outreach.crm import OutreachBlocked, check_transition, record
from atlas.outreach.generator import generate_drafts
from atlas.rank.scoring import compute_score, score_internship, score_tasks

T = "2026-10-11T00:00:00+00:00"
URL = "https://synthetic-co.example/about"


def claim(ctype, value, snippet="snippet", url=URL, conf=0.8):
    return {"claim_type": ctype, "value": value, "evidence_snippet": snippet, "source_url": url, "retrieved_at": T,
            "confidence": conf, "webpage_id": 1, "is_inference": 0}


def dossier(name="Synthetic Co", claims=(), pages=None, contacts=(), locations=(), sources=(), **company):
    pages = pages if pages is not None else [PageDoc(1, URL, "about", "About", "We design robots.", T, 200, None, False)]
    comp = {"id": 1, "name": name, "website": "https://synthetic-co.example/", "domain": "synthetic-co.example",
            "identity_status": "verified", **company}
    return Dossier(company=comp, claims=list(claims), pages=pages, contacts=list(contacts), locations=list(locations),
                   sources=list(sources))


ROBOT_CLAIMS = [
    claim("industry_signal", "robotics|strong|robotic"), claim("industry_signal", "robotics|strong|plc"),
    claim("industry_signal", "robotics|strong|machine vision"), claim("industry_signal", "robotics|strong|scada"),
    claim("activity", "We design robotic welding cells and machine vision systems"),
    claim("location_mention", "dubai_silicon_oasis"), claim("uae_presence", "UAE"),
    claim("copyright_year", "2026"), claim("hiring", "careers page"),
    claim("business_type_signal", "engineering_design|engineering services"),
]


def test_industry_score_rubric_monotonic_and_negative():
    base = industry_scores(dossier(claims=ROBOT_CLAIMS[:1]))
    more = industry_scores(dossier(claims=ROBOT_CLAIMS[:4]))
    r1 = next(s for s in base if s.industry == "robotics").score
    r4 = next(s for s in more if s.industry == "robotics").score
    assert 0 < r1 < r4 <= 100
    neg = industry_scores(dossier(name="Synthetic Rent A Car", claims=[
        claim("industry_signal", "automotive|medium|vehicles"), claim("industry_negative", "automotive|negative|car rental")]))
    assert next(s for s in neg if s.industry == "automotive").score == 0


def test_qualify_qualified_and_geo():
    d = dossier(claims=ROBOT_CLAIMS)
    q = qualify(d, SearchCriteria(industries=[Industry.ROBOTICS], areas=[AREAS["dubai"]]), QualificationSettings())
    assert q.status == QualificationStatus.QUALIFIED and q.geo_status == GeoStatus.IN_AREA
    assert q.reasons and q.industry_scores[0].evidence


def test_qualify_unknown_location_is_review_not_reject():
    claims = [c for c in ROBOT_CLAIMS if c["claim_type"] not in ("location_mention", "uae_presence")]
    q = qualify(dossier(claims=claims), SearchCriteria(industries=[Industry.ROBOTICS], areas=[AREAS["dubai"]]),
                QualificationSettings())
    assert q.geo_status == GeoStatus.UNKNOWN and q.status == QualificationStatus.NEEDS_REVIEW


def test_qualify_rejects_recruiter_and_outside():
    rec = dossier(name="Synthetic Recruitment", claims=ROBOT_CLAIMS + [
        claim("business_type_signal", "recruitment_agency|recruitment agency", "We are a recruitment agency."),
        claim("business_type_signal", "recruitment_agency|staffing solutions", "Staffing solutions for clients.")])
    q = qualify(rec, SearchCriteria(industries=[Industry.ROBOTICS], areas=[AREAS["dubai"]]), QualificationSettings())
    assert q.status == QualificationStatus.REJECTED
    far = dossier(claims=ROBOT_CLAIMS[:4], locations=[{"latitude": 51.5, "longitude": -0.12, "source_provider": "osm",
                                                       "country_code": "GB"}])
    q2 = qualify(far, SearchCriteria(industries=[Industry.ROBOTICS], areas=[AREAS["dubai"]]), QualificationSettings())
    assert q2.geo_status == GeoStatus.OUTSIDE and q2.status == QualificationStatus.REJECTED


def test_size_never_invented():
    q = qualify(dossier(claims=ROBOT_CLAIMS), SearchCriteria(industries=[Industry.ROBOTICS]), QualificationSettings())
    assert q.size_band == SizeBand.UNKNOWN and q.size_evidence is None
    assert size_band_for(9) == SizeBand.MICRO and size_band_for(1000) == SizeBand.ENTERPRISE


def _q(**kw):
    return qualify(dossier(claims=ROBOT_CLAIMS), SearchCriteria(industries=[Industry.ROBOTICS], areas=[AREAS["dubai"]]),
                   QualificationSettings()).model_copy(update=kw)


EMAIL_CONTACT = {"id": 1, "channel": "email", "value": "info@synthetic-co.example", "quality_score": 80,
                 "role_category": "general", "is_generic": 1, "source_type": "official_site", "name": None,
                 "verification_status": "publicly_published", "source_url": URL}


def test_internship_categories():
    prof = StudentProfile()
    base = ROBOT_CLAIMS
    # no programme, no disqualifier, channel → plausible speculative
    a = assess_internship(dossier(claims=base, contacts=[EMAIL_CONTACT]), _q(), prof)
    assert a.category == InternshipCategory.PLAUSIBLE and "minimum age (not stated)" in a.unknowns
    # explicit school-student wording → confirmed
    a = assess_internship(dossier(claims=base + [claim("eligibility_school_ok", "high school students")],
                                  contacts=[EMAIL_CONTACT]), _q(), prof)
    assert a.category == InternshipCategory.CONFIRMED
    # programme without eligibility info → unclear (never assume minors accepted)
    a = assess_internship(dossier(claims=base + [claim("internship_signal", "internship", conf=0.8)],
                                  contacts=[EMAIL_CONTACT]), _q(), prof)
    assert a.category == InternshipCategory.UNCLEAR
    # min age 18 → ineligible for a 17-year-old
    a = assess_internship(dossier(claims=base + [claim("eligibility_min_age", "18")], contacts=[EMAIL_CONTACT]), _q(), prof)
    assert a.category == InternshipCategory.INELIGIBLE
    # university-only programme → ineligible
    a = assess_internship(dossier(claims=base + [claim("internship_signal", "internship", conf=0.8),
                                                 claim("eligibility_university_only", "undergraduate students")],
                                  contacts=[EMAIL_CONTACT]), _q(), prof)
    assert a.category == InternshipCategory.INELIGIBLE
    # restricted environment flagged
    a = assess_internship(dossier(claims=base + [claim("restricted_environment", "security clearance")],
                                  contacts=[EMAIL_CONTACT]), _q(), prof)
    assert a.restricted_environment and a.safety_flags


def test_tasks_require_company_evidence_not_industry():
    prof = StudentProfile()
    generic = dossier(pages=[PageDoc(1, URL, "about", "About", "We are a robotics company. We love data and platforms.",
                                     T, 200, None, False)])
    tasks = find_tasks(generic, _q(), prof)
    assert all(t.template_key != "python_automation" for t in tasks)  # "data"/"platform" are only weak triggers
    rich = dossier(pages=[PageDoc(1, URL, "about", "About",
                                  "We keep a bill of materials for every cell and use SolidWorks. ISO 9001 inspection "
                                  "and test reports. We source components from suppliers.", T, 200, None, False)],
                   claims=[claim("hiring_role", "Document Controller", url="https://synthetic-co.example/careers")])
    tasks = find_tasks(rich, _q(), prof)
    keys = {t.template_key for t in tasks}
    assert {"bom_organisation", "cad_support", "qc_reporting"} <= keys
    bom = next(t for t in tasks if t.template_key == "bom_organisation")
    assert bom.basis == TaskBasis.OBSERVED and "recruiting" in bom.rationale
    inferred = [t for t in tasks if t.basis == TaskBasis.INFERRED]
    assert inferred and all("not a known need" in t.rationale for t in inferred)
    assert all(t.evidence for t in tasks)


def test_tasks_respect_student_skills():
    prof = StudentProfile(skills=[SkillEntry(name="Research")])
    rich = dossier(pages=[PageDoc(1, URL, "about", "About", "We use SolidWorks and procurement of spare parts.", T, 200,
                                  None, False)])
    keys = {t.template_key for t in find_tasks(rich, _q(), prof)}
    assert "cad_support" not in keys  # student has no CAD skill
    assert all(len(t.skills) for t in TEMPLATES)


async def test_email_verification_statuses(monkeypatch):
    async def fake(self, domain):
        return {"good.example": (True, "MX: mx.good.example"), "dead.example": (False, "NXDOMAIN")}.get(domain, (None, "timeout"))

    monkeypatch.setattr(DomainMailChecker, "_lookup", fake)
    chk = DomainMailChecker()
    v = await verify_email("info@good.example", checker=chk, site_domain="good.example", published_on_source=True)
    assert v.status == EmailStatus.PUBLISHED
    v = await verify_email("jane.roe@good.example", checker=chk, site_domain="good.example", published_on_source=True)
    assert v.status == EmailStatus.PUBLISHED_INDIVIDUAL
    assert any(c.check == "individual" and "cannot be verified" in c.detail for c in v.checks)
    v = await verify_email("info@dead.example", checker=chk, site_domain="dead.example", published_on_source=True)
    assert v.status == EmailStatus.INVALID
    v = await verify_email("info@slow.example", checker=chk, site_domain="slow.example", published_on_source=True)
    assert v.status == EmailStatus.SYNTAX_VALID  # never claims routing it could not check
    v = await verify_email("not-an-email", checker=chk, site_domain=None, published_on_source=None)
    assert v.status == EmailStatus.INVALID


def test_contact_quality_and_best_channel():
    assert channel_quality(EMAIL_CONTACT, relation="company_domain")[0] == 80
    invalid = {**EMAIL_CONTACT, "verification_status": "invalid"}
    assert channel_quality(invalid)[0] == 0
    form = {"id": 2, "channel": "contact_form", "value": URL, "quality_score": 60, "role_category": "general",
            "source_type": "official_site"}
    assert channel_quality(form)[0] == 60
    pick, why = best_contact([form], SizeBand.SMALL)
    assert pick["channel"] == "contact_form"  # companies with only a form stay eligible
    careers = {**EMAIL_CONTACT, "id": 3, "value": "careers@synthetic-co.example", "role_category": "hr_talent",
               "quality_score": 85}
    pick, why = best_contact([EMAIL_CONTACT, careers], SizeBand.ENTERPRISE)
    assert pick["id"] == 3 and "careers" in why.lower()


def test_scoring_rubrics_and_weights():
    from atlas.models.assessment import InternshipAssessment, TaskOpportunity
    from atlas.models.enums import Level, Supervision

    t = TaskOpportunity(template_key="x", title="t", description="d", rationale="r", basis=TaskBasis.OBSERVED,
                        skills=[], difficulty=Level.LOW, supervision=Supervision.MINIMAL, confidence=Level.HIGH,
                        outreach_angle="a")
    assert score_tasks([t])[0] == 45
    assert score_tasks([])[0] == 0
    ia = InternshipAssessment(category=InternshipCategory.PLAUSIBLE, uae_team=True)
    assert score_internship(ia, SizeBand.SMALL, True)[0] == 80
    d = dossier(claims=ROBOT_CLAIMS, contacts=[EMAIL_CONTACT])
    q = _q()
    w1 = ScoringWeights().normalized()
    s1 = compute_score(d, q, ia, [t], 80, w1)
    assert abs(sum(f.weight for f in s1.factors) - 1) < 1e-6
    assert s1.readiness_label == "Ready"
    # Missing contact does not make a strong company disappear
    s2 = compute_score(d, q, ia, [t], 0, w1)
    assert s2.total > 40 and s2.readiness_label == "Find a contact channel"
    # Weights change ranking deterministically
    w3 = ScoringWeights(industry=100, tasks=0, internship=0, contact=0, geography=0, activity=0).normalized()
    s3 = compute_score(d, q, ia, [t], 0, w3)
    assert s3.total == pytest.approx(next(f.score for f in s3.factors if f.key == "industry"), abs=0.1)
    blocked = compute_score(d, q, ia, [t], 80, w1, blocked_reason="opted out")
    assert blocked.outreach_readiness == 0 and blocked.contact_quality == 0


def test_outreach_drafts_grounded():
    d = dossier(claims=ROBOT_CLAIMS + [claim("offering", "Machine Vision Inspection", url="https://synthetic-co.example/services")],
                contacts=[EMAIL_CONTACT])
    q = _q()
    a = assess_internship(d, q, StudentProfile())
    prof = StudentProfile(full_name="Test Student", school="Synthetic School", email="student@synthetic.example")
    drafts = {dr.kind.value: dr for dr in generate_drafts(d, q, a, [], prof, EMAIL_CONTACT)}
    short = drafts["short_email"].body
    assert "Test Student" in short and "Synthetic School" in short and "17-year-old" in short
    assert "machine vision inspection" in short.lower()
    assert len(drafts["networking_message"].body) <= 300
    facts_text = " ".join(f.text for f in drafts["short_email"].facts) + " " + prof.model_dump_json()
    # No numbers or proper nouns beyond the company, its verified facts and the profile.
    for dr in drafts.values():
        new = introduces_new_facts(f"{d.name} {facts_text} Summer Dubai UAE United Arab Emirates", dr.body)
        assert not [w for w in new if not w.isdigit() or w not in ("2027",)], (dr.kind, new)
    # Incomplete profile → placeholders, never invented details
    blank = generate_drafts(d, q, a, [], StudentProfile(), EMAIL_CONTACT)[0]
    assert "[Your full name]" in blank.body and blank.warnings


def test_crm_rules(conn):
    cid = companies.create_company(conn, name="Synthetic Co", normalized_name="synthetic co", domain="synthetic-co.example")
    with pytest.raises(OutreachBlocked):
        record(conn, cid, OutreachStatus.CONTACTED)  # must confirm the message was actually sent
    record(conn, cid, OutreachStatus.CONTACTED, confirmed=True, contact_value="info@synthetic-co.example")
    chk = check_transition(conn, cid, OutreachStatus.CONTACTED)
    assert chk.requires_confirmation  # duplicate outreach warning
    record(conn, cid, OutreachStatus.DO_NOT_CONTACT, notes="asked not to be contacted")
    assert outreach.is_dnc(conn, company_id=cid)
    with pytest.raises(OutreachBlocked):
        record(conn, cid, OutreachStatus.CONTACTED, confirmed=True)


# ------------------------------------------------------------------ regressions from the first live run

def test_repeated_template_text_counts_once():
    one = [claim("industry_signal", "fintech|strong|fintech", url="https://synthetic-co.example/")]
    many = [claim("industry_signal", "fintech|strong|fintech", url=f"https://synthetic-co.example/p{i}") for i in range(9)]
    s1 = next(s for s in industry_scores(dossier(claims=one)) if s.industry == "fintech").score
    s9 = next(s for s in industry_scores(dossier(claims=many)) if s.industry == "fintech").score
    assert s1 == s9


def test_client_or_investor_mentions_do_not_set_business_type():
    from atlas.assess.qualification import business_type
    from atlas.models.enums import BusinessType

    d = dossier(name="Synthetic AI Co", claims=[claim("business_type_signal", "financial_firm|family office"),
                                               claim("business_type_signal", "software_product|book a demo")])
    assert business_type(d)[0] == BusinessType.SOFTWARE_PRODUCT


def test_zone_search_requires_zone_evidence():
    claims = [c for c in ROBOT_CLAIMS if c["claim_type"] != "location_mention"] + [claim("location_mention", "dubai")]
    crit = SearchCriteria(industries=[Industry.ROBOTICS], areas=[AREAS["difc"]])
    q = qualify(dossier(claims=claims), crit, QualificationSettings())
    assert q.geo_status == GeoStatus.NEARBY and q.status == QualificationStatus.NEEDS_REVIEW
    crit2 = SearchCriteria(industries=[Industry.ROBOTICS], areas=[AREAS["dubai"]])
    assert qualify(dossier(claims=claims), crit2, QualificationSettings()).status == QualificationStatus.QUALIFIED


def test_structured_hq_city_counts_for_geography():
    claims = [c for c in ROBOT_CLAIMS if c["claim_type"] not in ("location_mention", "uae_presence")]
    d = dossier(claims=claims, locations=[{"latitude": None, "longitude": None, "city": "dubai", "address": None,
                                           "source_provider": "wikidata", "country_code": "AE"}])
    q = qualify(d, SearchCriteria(industries=[Industry.ROBOTICS], areas=[AREAS["dubai"]]), QualificationSettings())
    assert q.geo_status == GeoStatus.IN_AREA


def test_business_type_weak_phrases_and_self_description():
    from atlas.assess.qualification import business_type
    from atlas.models.enums import BusinessType

    # "we produce the ads" / partner "manufacturer of" must not make a marketing agency a manufacturer
    d = dossier(name="Synthetic Agency", claims=[claim("business_type_signal", "manufacturer|~manufacturer of"),
                                                 claim("business_type_signal", "software_product|book a demo")])
    assert business_type(d)[0] == BusinessType.SOFTWARE_PRODUCT
    # UAE "General Trading" licence name is strong self-description
    gt = dossier(name="Synthetic General Trading LLC", claims=[claim("business_type_signal", "software_product|book a demo")])
    assert business_type(gt)[0] == BusinessType.DISTRIBUTOR_TRADING
    # schema.org types declared by the site count
    sc = dossier(name="Synthetic Staffing", claims=[claim("structured_type", "EmploymentAgency")])
    assert business_type(sc)[0] == BusinessType.RECRUITMENT_AGENCY


def test_draft_wording_casing_and_calls_to_action():
    from atlas.outreach.generator import _clean_offering, _fix_case, _offerings

    assert _fix_case("generative ai") == "generative AI"
    assert _clean_offering("Crypto CFDs") == "crypto CFDs"
    assert _clean_offering("PLC Programming") == "PLC programming"
    d = dossier(claims=[claim("offering", "Transfer funds securely, free of charge"),
                        claim("offering", "Machine Vision Inspection")])
    assert [o for o, _ in _offerings(d)] == ["machine vision inspection"]
