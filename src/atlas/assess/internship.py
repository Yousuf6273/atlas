"""Internship suitability for a school-age (under-18) student.

Categories
----------
* explicitly_ineligible — the company states a minimum age above the student's age, or its programme
  explicitly requires university enrolment / a degree (with no school-student route).
* confirmed_opportunity — the company explicitly welcomes school students (work experience, high-school
  internships, A-level/IB students, ages 15–17 …).
* eligibility_unclear — a programme exists but eligibility for school students is not stated; or the site
  could not be researched; or the work environment is restricted/safety-critical.
* plausible_speculative — no programme and no disqualifier; the company is relevant, has a UAE team and
  direct outreach is practical. This is a *hypothesis* for speculative outreach, not a claim.

ATLAS never assumes a company accepts minors.
"""

from __future__ import annotations

from atlas.assess.dossier import Dossier
from atlas.models.assessment import InternshipAssessment, QualificationResult
from atlas.models.company import Evidence
from atlas.models.enums import (
    GeoStatus,
    InternshipCategory,
    QualificationStatus,
    SizeBand,
)
from atlas.models.profile import StudentProfile


def _ev(c: dict) -> Evidence:
    return Evidence(source_url=c["source_url"], snippet=c["evidence_snippet"], retrieved_at=c["retrieved_at"])


def assess_internship(d: Dossier, q: QualificationResult, profile: StudentProfile) -> InternshipAssessment:
    prog = d.claims_of("internship_signal")
    school = d.claims_of("eligibility_school_ok")
    uni = d.claims_of("eligibility_university_only")
    ages = [int(c["value"]) for c in d.claims_of("eligibility_min_age") if c["value"].isdigit()]
    restricted = d.claims_of("restricted_environment")
    has_programme = any(c["confidence"] >= 0.75 for c in prog) or len(prog) >= 2
    min_age = max(ages) if ages else None
    age = profile.age
    uae_team = None if q.geo_status == GeoStatus.UNKNOWN else q.geo_status in (
        GeoStatus.IN_AREA, GeoStatus.NEARBY, GeoStatus.IN_COUNTRY)
    has_channel = any(c["channel"] in ("email", "contact_form", "careers_page") for c in d.contacts)
    direct_practical = has_channel and q.size_band in (SizeBand.MICRO, SizeBand.SMALL, SizeBand.MEDIUM, SizeBand.UNKNOWN)
    evidence = [_ev(c) for c in (prog[:2] + school[:2] + uni[:2] + d.claims_of("eligibility_min_age")[:1])]
    flags = [f"{c['value']}: “{c['evidence_snippet'][:120]}”" for c in restricted[:3]]
    reasons: list[str] = []
    unknowns: list[str] = []

    if not school:
        unknowns.append("whether under-18 / school students are accepted")
    if min_age is None:
        unknowns.append("minimum age (not stated)")
    if not has_programme:
        unknowns.append("whether short or summer placements are offered")

    if min_age is not None and age is not None and age < min_age:
        cat = InternshipCategory.INELIGIBLE
        reasons.append(f"States a minimum age of {min_age}; the student is {age}.")
    elif uni and not school:
        cat = InternshipCategory.INELIGIBLE
        reasons.append("Advertised internship requires university enrolment or a degree.")
        reasons.append("A speculative request outside the programme is possible but unlikely to succeed.")
    elif school:
        cat = InternshipCategory.CONFIRMED
        reasons.append("The company explicitly mentions opportunities for school students.")
    elif has_programme:
        cat = InternshipCategory.UNCLEAR
        reasons.append("An internship/student programme is mentioned, but eligibility for school students is not stated.")
    elif not d.good_pages:
        cat = InternshipCategory.UNCLEAR
        reasons.append("The official website could not be researched, so eligibility cannot be assessed.")
    elif restricted:
        cat = InternshipCategory.UNCLEAR
        reasons.append("The work involves restricted or safety-critical environments; minors are likely restricted.")
    elif q.status == QualificationStatus.REJECTED:
        cat = InternshipCategory.UNCLEAR
        reasons.append("Company not qualified for this search; internship suitability not assessed further.")
    elif uae_team is False:
        cat = InternshipCategory.UNCLEAR
        reasons.append("No evidence of a team in the UAE.")
    elif direct_practical:
        cat = InternshipCategory.PLAUSIBLE
        reasons.append("No internship programme advertised and no disqualifying rules found; relevant company with "
                       "a direct contact channel — suitable for a polite speculative enquiry.")
    else:
        cat = InternshipCategory.UNCLEAR
        if not has_channel:
            reasons.append("No usable contact channel was found for a direct enquiry.")
        else:
            reasons.append("Large organisation without an advertised route for school students; a direct approach "
                           "is less practical.")
    if restricted and cat != InternshipCategory.INELIGIBLE:
        reasons.append("Safety note: avoid proposing any hands-on work in restricted areas; office-based tasks only.")
    if has_programme and cat == InternshipCategory.CONFIRMED:
        reasons.append("A formal programme is also mentioned.")

    return InternshipAssessment(
        category=cat, has_programme=has_programme, school_students_welcome=bool(school), university_only=bool(uni),
        min_age=min_age, restricted_environment=bool(restricted), uae_team=uae_team,
        direct_outreach_practical=direct_practical, reasons=reasons, evidence=evidence, safety_flags=flags,
        unknowns=unknowns,
    )
