"""Transparent prospect ranking.

All factor scores are deterministic rubrics on evidence — no model confidence
values are used as probabilities.

Factor rubrics (0–100)
----------------------
industry   = qualification relevance × business-type multiplier
             (design/manufacturer/R&D/software/integrator/financial 1.0; services/consultancy 0.8;
              operator 0.7; unknown 0.85; distributor 0.55; recruitment/training/retail/government 0.1)
tasks      = 45·t1 + 30·t2 + 25·t3 over the three best tasks, where t = confidence (high 1.0, medium 0.7,
             low 0.4) × skill match, +20 % for an observed need (capped at 1)
internship = confirmed 100; plausible 60 (+10 small/micro, +5 named decision-maker, +5 UAE team confirmed);
             unclear 40; ineligible 5; −20 if restricted environment
contact    = quality score of the best usable channel (see contacts/quality.py)
geography  = in area 100; nearby 70; in country 50; unknown 35; outside 0
activity   = website live with content 40 + recent copyright/dated content 30 + hiring 20 + ≥ 2 sources 10

Separate dimensions
-------------------
suitability         weighted mean of industry, tasks, internship, geography (contact excluded)
evidence_confidence how much was actually researched (pages, sourced claims, sources, identity check)
outreach_readiness  qualified + usable channel + not blocked + not already contacted
"""

from __future__ import annotations

from atlas.assess.dossier import Dossier
from atlas.models.assessment import (
    FactorScore,
    InternshipAssessment,
    QualificationResult,
    ScoreBreakdown,
    TaskOpportunity,
)
from atlas.models.enums import (
    BusinessType,
    GeoStatus,
    InternshipCategory,
    Level,
    QualificationStatus,
    SizeBand,
    TaskBasis,
)

BT_MULT = {
    BusinessType.ENGINEERING_DESIGN: 1.0, BusinessType.MANUFACTURER: 1.0, BusinessType.RND_TECHNOLOGY: 1.0,
    BusinessType.SOFTWARE_PRODUCT: 1.0, BusinessType.SYSTEMS_INTEGRATOR: 1.0, BusinessType.FINANCIAL_FIRM: 1.0,
    BusinessType.SERVICE_MAINTENANCE: 0.8, BusinessType.CONSULTANCY: 0.8, BusinessType.OPERATOR: 0.7,
    BusinessType.UNKNOWN: 0.85, BusinessType.DISTRIBUTOR_TRADING: 0.55, BusinessType.RECRUITMENT_AGENCY: 0.1,
    BusinessType.TRAINING_PROVIDER: 0.1, BusinessType.RETAIL_CONSUMER: 0.1, BusinessType.GOVERNMENT_PUBLIC: 0.1,
}
CONF_VAL = {Level.HIGH: 1.0, Level.MEDIUM: 0.7, Level.LOW: 0.4}
GEO = {GeoStatus.IN_AREA: 100, GeoStatus.NEARBY: 70, GeoStatus.IN_COUNTRY: 50, GeoStatus.UNKNOWN: 35,
       GeoStatus.OUTSIDE: 0}
LABELS = {"industry": "Industry & technical relevance", "tasks": "Useful student tasks",
          "internship": "Internship accessibility", "contact": "Contact quality",
          "geography": "Geographic suitability", "activity": "Evidence of company activity"}


def score_tasks(tasks: list[TaskOpportunity]) -> tuple[float, str]:
    vals = []
    for t in tasks:
        v = CONF_VAL[t.confidence] * t.skill_match * (1.2 if t.basis == TaskBasis.OBSERVED else 1.0)
        vals.append(min(1.0, v))
    vals.sort(reverse=True)
    vals += [0.0, 0.0, 0.0]
    s = 45 * vals[0] + 30 * vals[1] + 25 * vals[2]
    obs = sum(1 for t in tasks if t.basis == TaskBasis.OBSERVED)
    return round(s, 1), (f"{len(tasks)} evidence-based task idea(s)" + (f", {obs} backed by an observed need" if obs else "")
                         if tasks else "no task ideas supported by the company's own text")


def score_internship(a: InternshipAssessment | None, size: SizeBand, has_named_decision_maker: bool) -> tuple[float, str]:
    if a is None:
        return 30.0, "not assessed"
    if a.category == InternshipCategory.CONFIRMED:
        s, why = 100.0, "explicitly open to school students"
    elif a.category == InternshipCategory.PLAUSIBLE:
        s, why = 60.0, "speculative outreach plausible (no programme, no disqualifier)"
        if size in (SizeBand.MICRO, SizeBand.SMALL):
            s += 10
            why += "; small company"
        if has_named_decision_maker:
            s += 5
            why += "; named decision-maker published"
        if a.uae_team:
            s += 5
    elif a.category == InternshipCategory.UNCLEAR:
        s, why = 40.0, "eligibility unclear"
    else:
        s, why = 5.0, "explicitly ineligible (age or university requirement)"
    if a.restricted_environment:
        s -= 20
        why += "; restricted/safety-critical environment"
    return max(0.0, min(100.0, s)), why


def score_activity(d: Dossier) -> tuple[float, str]:
    s = 0.0
    parts = []
    words = sum(len(p.text.split()) for p in d.good_pages)
    if d.good_pages and words >= 150:
        s += 40
        parts.append("website live")
    if d.claims_of("copyright_year", "dated_content"):
        from datetime import date

        yrs = [int(c["value"][:4]) for c in d.claims_of("copyright_year", "dated_content") if c["value"][:4].isdigit()]
        if yrs and max(yrs) >= date.today().year - 1:
            s += 30
            parts.append(f"recent content ({max(yrs)})")
    if d.claims_of("hiring", "hiring_role"):
        s += 20
        parts.append("hiring")
    if len(d.providers()) >= 2:
        s += 10
        parts.append(f"{len(d.providers())} sources")
    return s, ", ".join(parts) or "little evidence of current activity"


def evidence_confidence(d: Dossier) -> float:
    pages = len(d.good_pages)
    sourced = len([c for c in d.claims if c.get("source_url")])
    s = min(40, pages * 8) + min(30, sourced * 1.5) + min(15, len(d.providers()) * 7.5)
    if d.company.get("identity_status") == "verified":
        s += 15
    return round(min(100.0, s), 1)


def compute_score(d: Dossier, q: QualificationResult, a: InternshipAssessment | None, tasks: list[TaskOpportunity],
                  best_contact_quality: float, weights: dict[str, float], *, blocked_reason: str | None = None,
                  already_contacted: bool = False) -> ScoreBreakdown:
    ind = q.target_relevance * BT_MULT.get(q.business_type, 0.85)
    ts, ts_why = score_tasks(tasks)
    named = any(c["channel"] in ("named_person", "email") and c.get("name") for c in d.contacts)
    ins, ins_why = score_internship(a, q.size_band, named)
    geo = float(GEO[q.geo_status])
    act, act_why = score_activity(d)
    contact = 0.0 if blocked_reason else best_contact_quality
    raw = {"industry": ind, "tasks": ts, "internship": ins, "contact": contact, "geography": geo, "activity": act}
    expl = {
        "industry": f"relevance {q.target_relevance:.0f} × {BT_MULT.get(q.business_type, 0.85):.2f} ({q.business_type.value})",
        "tasks": ts_why, "internship": ins_why,
        "contact": ("blocked: " + blocked_reason) if blocked_reason else f"best channel quality {contact:.0f}",
        "geography": q.geo_status.value.replace("_", " "), "activity": act_why,
    }
    factors = [FactorScore(key=k, label=LABELS[k], score=round(v, 1), weight=round(weights.get(k, 0), 4),
                           explanation=expl[k]) for k, v in raw.items()]
    total = sum(f.score * f.weight for f in factors)
    suit_keys = ("industry", "tasks", "internship", "geography")
    sw = sum(weights.get(k, 0) for k in suit_keys) or 1
    suitability = sum(raw[k] * weights.get(k, 0) for k in suit_keys) / sw
    notes = []
    if q.status == QualificationStatus.REJECTED:
        total *= 0.3
        notes.append("rejected companies are down-weighted ×0.3")
    readiness, label = _readiness(q, contact, blocked_reason, already_contacted, a)
    return ScoreBreakdown(total=round(total, 1), factors=factors, suitability=round(suitability, 1),
                          evidence_confidence=evidence_confidence(d), contact_quality=round(contact, 1),
                          outreach_readiness=readiness, readiness_label=label, notes=notes)


def _readiness(q: QualificationResult, contact: float, blocked: str | None, contacted: bool,
               a: InternshipAssessment | None) -> tuple[float, str]:
    if blocked:
        return 0.0, "Blocked (do not contact)"
    if contacted:
        return 0.0, "Already contacted"
    if q.status == QualificationStatus.REJECTED:
        return 0.0, "Not qualified"
    if a is not None and a.category == InternshipCategory.INELIGIBLE:
        return 10.0, "Ineligible programme"
    if q.status == QualificationStatus.NEEDS_REVIEW:
        return 30.0 if contact >= 50 else 15.0, "Needs review first"
    if contact >= 60:
        return 100.0, "Ready"
    if contact > 0:
        return 70.0, "Ready (weak channel)"
    return 40.0, "Find a contact channel"
