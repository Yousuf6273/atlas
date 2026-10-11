"""Run all assessment engines for one company and persist the results."""

from __future__ import annotations

from dataclasses import dataclass

from atlas.assess.dossier import Dossier, load_dossier
from atlas.assess.internship import assess_internship
from atlas.assess.qualification import qualify
from atlas.assess.tasks import find_tasks
from atlas.contacts.quality import best_contact, channel_quality
from atlas.contacts.verify import verify_email
from atlas.db import assessments, companies, contacts, outreach, review
from atlas.extract.contacts import email_domain_relation
from atlas.models.enums import SENT_STATUSES, QualificationStatus
from atlas.models.search import SearchCriteria
from atlas.outreach.crm import current_status
from atlas.outreach.generator import generate_drafts
from atlas.pipeline.context import PipelineContext
from atlas.rank.scoring import compute_score


@dataclass
class AssessOutcome:
    company_id: int
    status: QualificationStatus
    total: float
    internship: str
    tasks: int


async def verify_contacts(ctx: PipelineContext, d: Dossier) -> None:
    site_domain = d.company.get("domain")
    for c in d.contacts:
        relation = None
        if c["channel"] == "email":
            published = None
            if c["source_type"] == "official_site":
                # Official-site contacts were extracted during this crawl from the cited page's HTML (visible
                # text, a mailto: link or structured data), so the cited page is their source.
                published = bool(c.get("evidence"))
            v = await verify_email(c["value"], checker=ctx.mail_checker, site_domain=site_domain,
                                   published_on_source=published, check_mx=ctx.cfg.verification.check_mx)
            contacts.save_verification(ctx.conn, c["id"], v)
            c["verification_status"] = v.status.value
            relation = email_domain_relation(c["value"], site_domain)
        score, _why = channel_quality(c, relation=relation)
        if outreach.is_dnc(ctx.conn, company_id=d.id, email=c["value"] if c["channel"] == "email" else None):
            score = 0.0
        contacts.set_quality(ctx.conn, c["id"], score)


async def assess_company(ctx: PipelineContext, company_id: int, criteria: SearchCriteria, *,
                         make_drafts: bool = True) -> AssessOutcome:
    conn, cfg = ctx.conn, ctx.cfg
    d = load_dossier(conn, company_id)
    q = qualify(d, criteria, cfg.qualification)
    assessments.save_qualification(conn, company_id, ctx.job_id, q)
    for field_name, value in (("primary_industry", q.primary_industry), ("business_type", q.business_type.value),
                              ("size_band", q.size_band.value), ("size_evidence", q.size_evidence)):
        if value is not None:
            companies.set_field(conn, company_id, field_name, value, confidence=0.6, source="rules")

    await verify_contacts(ctx, d)
    d = load_dossier(conn, company_id)  # refreshed contact statuses/scores
    a = assess_internship(d, q, ctx.profile)
    assessments.save_internship(conn, company_id, a)
    tasks = find_tasks(d, q, ctx.profile)
    assessments.save_tasks(conn, company_id, tasks)

    pick, _strategy = best_contact(d.contacts, q.size_band, has_programme=a.has_programme)
    blocked = outreach.is_dnc(conn, company_id=company_id, domain=d.company.get("domain"))
    contacted = current_status(conn, company_id) in SENT_STATUSES
    best_q = float(pick["quality_score"]) if pick else 0.0
    score = compute_score(d, q, a, tasks, best_q, cfg.scoring.normalized(), blocked_reason=blocked,
                          already_contacted=contacted)
    assessments.save_score(conn, company_id, ctx.job_id, score, cfg.scoring.normalized())

    if q.status == QualificationStatus.NEEDS_REVIEW:
        review.enqueue(conn, item_type="qualification", company_id=company_id,
                       reason="; ".join(r for r in q.reasons if r.startswith("Needs review"))[:300] or "borderline",
                       score=q.target_relevance, details={"uncertainties": q.uncertainties})
    if d.company.get("identity_status") == "mismatch":
        review.enqueue(conn, item_type="identity", company_id=company_id,
                       reason=d.company.get("identity_notes") or "website identity mismatch")

    if make_drafts and q.status != QualificationStatus.REJECTED and not blocked:
        for draft in generate_drafts(d, q, a, tasks, ctx.profile, pick):
            outreach.save_draft(conn, company_id, kind=draft.kind.value, subject=draft.subject, body=draft.body,
                                facts=[f.as_dict() for f in draft.facts] + [{"kind": "warning", "text": w}
                                                                           for w in draft.warnings],
                                method="template", contact_id=pick["id"] if pick and pick.get("id") else None)
    companies.mark_researched(conn, company_id, qualification_status=q.status.value,
                              internship_category=a.category.value)
    return AssessOutcome(company_id, q.status, score.total, a.category.value, len(tasks))


def rescore_company(ctx: PipelineContext, company_id: int) -> float | None:
    """Recompute only the ranking (e.g. after changing weights) from stored assessments, for every search
    the company has a current assessment in."""
    d = load_dossier(ctx.conn, company_id)
    a = assessments.current_internship(ctx.conn, company_id)
    tasks = assessments.current_tasks(ctx.conn, company_id)
    blocked = outreach.is_dnc(ctx.conn, company_id=company_id, domain=d.company.get("domain"))
    contacted = current_status(ctx.conn, company_id) in SENT_STATUSES
    weights = ctx.cfg.scoring.normalized()
    total = None
    for job_id in assessments.current_assessment_jobs(ctx.conn, company_id):
        q = assessments.current_qualification(ctx.conn, company_id, job_id)
        if q is None:
            continue
        pick, _ = best_contact(d.contacts, q.size_band, has_programme=bool(a and a.has_programme))
        s = compute_score(d, q, a, tasks, float(pick["quality_score"]) if pick else 0.0, weights,
                          blocked_reason=blocked, already_contacted=contacted)
        assessments.save_score(ctx.conn, company_id, job_id, s, weights)
        total = s.total
    return total
