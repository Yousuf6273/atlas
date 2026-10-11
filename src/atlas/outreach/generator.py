"""Personalised outreach drafts (deterministic templates).

Grounding rules
---------------
* Company-specific content comes only from claims extracted from the company's
  own website (offerings, activity sentences, programme mentions), each listed
  in the draft's ``facts`` with its source URL so the student can check it.
* Student content comes only from the editable profile. Missing profile fields
  become visible ``[placeholders]`` — never invented.
* Task suggestions are phrased as offers ("I could help with …, if useful"),
  never as statements about the company's internal problems.
* Nothing is sent: drafts must be reviewed, edited and approved by the student.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from atlas.assess.dossier import Dossier
from atlas.models.assessment import (
    InternshipAssessment,
    QualificationResult,
    TaskOpportunity,
)
from atlas.models.enums import (
    INDUSTRY_LABELS,
    DraftKind,
    InternshipCategory,
    RoleCategory,
    SizeBand,
)
from atlas.models.profile import StudentProfile

FOCUS_SHORT = {
    "aerospace": "aerospace engineering", "mechanical": "mechanical engineering", "robotics": "robotics and automation",
    "automotive": "automotive engineering", "manufacturing": "manufacturing", "ai_ml": "AI and machine learning",
    "software": "software engineering", "data_analytics": "data analytics", "fintech": "financial technology",
    "quant_finance": "quantitative finance",
}
TASK_SHORT = {
    "supplier_research": "putting together a supplier or component comparison",
    "bom_organisation": "organising parts lists or bills of materials",
    "spreadsheet_automation": "automating a repetitive spreadsheet or report",
    "qc_reporting": "organising quality-control data into a clear log",
    "technical_documentation": "organising datasheets and technical documents",
    "cad_support": "basic, supervised CAD or drawing-register work",
    "python_automation": "writing small Python scripts for data tasks",
    "software_testing": "testing your product and writing clear bug reports",
    "dataset_preparation": "cleaning or labelling datasets",
    "competitor_research": "a short competitor or market overview",
    "inventory_data": "tidying inventory or spare-parts records",
    "dashboard_creation": "a simple KPI dashboard",
    "robotics_test_logging": "logging tests or researching components",
    "flight_data_analysis": "cleaning and plotting flight or telemetry logs",
    "maintenance_records": "digitising maintenance records",
    "financial_data_analysis": "cleaning market data for a simple analysis",
    "research_summaries": "short, sourced research summaries",
}


@dataclass
class Fact:
    kind: str  # offering | activity | programme | task | profile
    text: str
    source_url: str | None = None
    note: str | None = None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text, "source_url": self.source_url, "note": self.note}


@dataclass
class DraftOut:
    kind: DraftKind
    subject: str | None
    body: str
    facts: list[Fact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _p(value: str, placeholder: str) -> str:
    return value.strip() if value and value.strip() else f"[{placeholder}]"


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


ACRONYMS = {"ai": "AI", "ml": "ML", "nlp": "NLP", "saas": "SaaS", "plc": "PLC", "plcs": "PLCs", "scada": "SCADA",
            "cnc": "CNC", "mro": "MRO", "uav": "UAV", "uavs": "UAVs", "bim": "BIM", "mep": "MEP", "hvac": "HVAC",
            "iot": "IoT", "iiot": "IIoT", "ev": "EV", "evs": "EVs", "fea": "FEA", "cfd": "CFD", "cfds": "CFDs",
            "erp": "ERP", "crm": "CRM", "llm": "LLM", "llms": "LLMs", "api": "API", "apis": "APIs", "bi": "BI",
            "agv": "AGV", "agvs": "AGVs", "amr": "AMR", "amrs": "AMRs", "qa": "QA", "qc": "QC", "ndt": "NDT",
            "rpa": "RPA", "3d": "3D", "gcc": "GCC", "uae": "UAE", "easa": "EASA", "gcaa": "GCAA"}
OFFERING_VERBS = re.compile(r"^(transfer|trade|invest|access|open|manage|protect|secure|send|pay|save|earn|buy|sell|"
                            r"track|automate|optimi[sz]e|connect|create|deliver|get|go|try|book|request|contact|"
                            r"download|watch|read|view|apply|enjoy|experience|elevate|accelerate|simplify|reimagine)\b",
                            re.IGNORECASE)


def _fix_case(text: str) -> str:
    """Restore acronym casing in lower-cased taxonomy terms ("generative ai" → "generative AI")."""
    return " ".join(ACRONYMS.get(w.lower(), w) if w.islower() else w for w in text.split())


def _clean_offering(h: str) -> str:
    h = re.sub(r"\s+", " ", h).strip(" .:-–|")
    out = []
    for w in h.split():
        if w.lower() in ACRONYMS:
            out.append(ACRONYMS[w.lower()])
        elif (w.isupper() and len(w) > 1) or any(ch.isdigit() for ch in w) or any(ch.isupper() for ch in w[1:]):
            out.append(w)  # acronyms and mixed case (e.g. "CFDs", "iOS") keep their casing
        else:
            out.append(w.lower())
    return " ".join(out)


def _activity_sentence(d: Dossier) -> tuple[str, str] | None:
    """An activity sentence that reads naturally after 'I read that you …'."""
    first = d.name.split()[0].lower()
    for c in sorted(d.claims_of("activity"), key=lambda c: -c["confidence"]):
        s = " ".join(c["value"].split())
        words = s.split()
        if not (8 <= len(words) <= 32) or "…" in s:
            continue
        m = re.match(r"^(We|At [^,]+, we|Our (company|team))\s+(.*)$", s)
        if m:
            rest = m.group(3).rstrip(".")
            if m.group(1).startswith("Our"):
                return f"your {m.group(1).split()[1]} {rest}", c["source_url"]
            return f"you {rest}", c["source_url"]
        if s.lower().startswith(first):
            return s.rstrip("."), c["source_url"]
    return None


def _offerings(d: Dossier, limit: int = 2) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    from atlas.extract.signals import SLOGAN

    for c in d.claims_of("offering"):
        o = _clean_offering(c["value"])
        if SLOGAN.search(c["value"]) or OFFERING_VERBS.search(c["value"]) or "," in c["value"] or len(o.split()) > 5:
            continue  # slogans and calls to action read badly in "your work in …"
        if 2 <= len(o) <= 60 and o not in seen:
            seen.add(o)
            out.append((o, c["source_url"]))
        if len(out) >= limit:
            break
    return out


def _greeting(contact: dict | None, company: str) -> tuple[str, str | None]:
    if contact and contact.get("name"):
        return f"Dear {contact['name']},", f"named contact published on the company website: {contact['name']}"
    role = (contact or {}).get("role_category")
    if role == RoleCategory.HR_TALENT.value:
        return "Dear Hiring Team,", None
    return f"Dear {company} team,", None


PROPER_SKILL_WORDS = {"python", "excel", "cad", "solidworks", "autocad", "matlab", "sql", "vba", "fusion", "onshape",
                      "java", "javascript", "c++", "r", "arduino", "raspberry", "pi", "power", "bi", "tableau", "git"}


def _fmt_skill(name: str) -> str:
    return " ".join(w if (w.lower() in PROPER_SKILL_WORDS or w.isupper()) else w.lower() for w in name.split())


def _skills_sentence(profile: StudentProfile, detailed: bool) -> str:
    names = [s.name for s in profile.skills]
    if not names:
        return ""
    base = f"Skills I can offer include {_join([_fmt_skill(n) for n in names[:5]])}."
    if detailed:
        examples = [f"{s.name}: {s.evidence.strip().rstrip('.')}" for s in profile.skills if s.evidence.strip()]
        if examples:
            base += " For example — " + "; ".join(examples[:2]) + "."
    return base


def _intro(profile: StudentProfile) -> str:
    age = f"{profile.age}-year-old " if profile.age else ""
    school = f" at {profile.school.strip()}" if profile.school.strip() else " at [your school]"
    subjects = _join(profile.subjects) if profile.subjects else "[your subjects]"
    return (f"My name is {_p(profile.full_name, 'Your full name')}, and I'm a {age}{profile.education_level}"
            f"{school} in {profile.location_city}, studying {subjects}.")


def _signoff(profile: StudentProfile) -> str:
    lines = ["Kind regards,", _p(profile.full_name, "Your full name")]
    contact_bits = [b for b in (profile.phone.strip(), profile.email.strip()) if b]
    lines.append(" | ".join(contact_bits) if contact_bits else "[your phone] | [your email]")
    for url in (profile.linkedin_url, profile.github_url, profile.portfolio_url):
        if url.strip():
            lines.append(url.strip())
    return "\n".join(lines)


def _variant(company_id: int, options: list[str]) -> str:
    return options[company_id % len(options)]


def generate_drafts(d: Dossier, q: QualificationResult, a: InternshipAssessment | None, tasks: list[TaskOpportunity],
                    profile: StudentProfile, contact: dict | None) -> list[DraftOut]:
    company = d.name
    facts: list[Fact] = []
    warnings: list[str] = []
    missing = profile.is_complete_for_outreach()
    if missing:
        warnings.append("Profile incomplete (" + ", ".join(missing) + "): placeholders in [brackets] must be filled in.")

    focus = FOCUS_SHORT.get(q.primary_industry or "", None) or (
        INDUSTRY_LABELS.get(q.primary_industry or "", "engineering").lower())
    offerings = _offerings(d)
    for o, url in offerings:
        facts.append(Fact("offering", o, url))
    activity = _activity_sentence(d)
    if activity:
        facts.append(Fact("activity", activity[0], activity[1]))
    if offerings:
        why_company = f"your work in {_join([o for o, _ in offerings])}"
    elif q.industry_scores and q.industry_scores[0].matched_terms:
        terms = [_fix_case(t) for t in q.industry_scores[0].matched_terms if ":" not in t][:2]
        why_company = f"your work in {_join(terms)}" if terms else f"your work in {focus}"
        for t in terms:
            facts.append(Fact("activity", t, q.industry_scores[0].evidence[0].source_url
                              if q.industry_scores[0].evidence else d.company.get("website")))
    else:
        why_company = f"your work in {focus}"
        warnings.append("No specific company activity was extracted; personalise the 'why this company' line yourself.")

    top_tasks = [t for t in tasks if t.template_key in TASK_SHORT][:2]
    for t in top_tasks:
        facts.append(Fact("task", t.title, t.evidence[0].source_url if t.evidence else None,
                          note=f"{t.basis.value.replace('_', ' ')} · confidence {t.confidence.value}"))
    task_phrase = _join([TASK_SHORT[t.template_key] for t in top_tasks])
    programme_line = None
    if a is not None and a.category == InternshipCategory.CONFIRMED and a.evidence:
        ev = a.evidence[0]
        facts.append(Fact("programme", ev.snippet[:200], ev.source_url))
        programme_line = "I saw on your website that you offer opportunities for school students, and I'd like to apply."
    greeting, greet_fact = _greeting(contact, company)
    if greet_fact:
        facts.append(Fact("contact", greet_fact, contact.get("source_url") if contact else None))
    facts.append(Fact("profile", "Student details are taken from your profile (Settings → Profile)."))

    year = profile.summer_year
    placement = profile.preferred_placement
    humility = None
    if a is None or a.category in (InternshipCategory.PLAUSIBLE, InternshipCategory.UNCLEAR):
        humility = ("I understand you may not normally take school students, so I'd be grateful for any guidance even "
                    "if a placement isn't possible.")
    small = q.size_band in (SizeBand.MICRO, SizeBand.SMALL)
    subject = f"Summer {year} work experience – A-level student interested in {focus}"

    # ---------------------------------------------------------------- short email
    opener = _variant(d.id, [
        f"I'm writing because I'm very interested in {focus}, and {why_company} caught my attention.",
        f"I came across {company} while researching {focus} companies in the UAE, and {why_company} stood out to me.",
        f"I'm keen to get real experience in {focus}, and {why_company} is exactly the kind of work I'd like to learn about.",
    ])
    ask = (f"Would you consider me for {placement} in summer {year}?")
    offer = (f" I'd be glad to help with something practical — for example {task_phrase} — if that would be useful."
             if task_phrase else "")
    cv = " I've attached my CV." if profile.cv_attached_note else ""
    short_body = "\n\n".join(x for x in [
        greeting,
        _intro(profile) + " " + opener,
        programme_line or (ask + offer),
        (ask + offer) if programme_line else (humility or ""),
        f"Thank you for your time.{cv}",
        _signoff(profile),
    ] if x)

    # ---------------------------------------------------------------- detailed email
    act_line = f"I read on your website that {activity[0]}." if activity else ""
    proj = ""
    if profile.projects:
        p0 = profile.projects[0]
        proj = f"Recently I worked on {p0.title}: {p0.description.strip().rstrip('.')}."
    task_details = ""
    if top_tasks:
        bits = [f"{TASK_SHORT[t.template_key]}" for t in top_tasks]
        task_details = ("Looking at what you do, a few things I could realistically help with under supervision are "
                        f"{_join(bits)}. I'd be happy to take on whatever is most useful, including routine tasks.")
    detailed_body = "\n\n".join(x for x in [
        greeting,
        _intro(profile) + f" I'm particularly interested in {focus}.",
        " ".join(y for y in [act_line, f"That is why {company} stood out to me." if act_line else
                             f"{why_company[0].upper() + why_company[1:]} is a big part of why {company} stood out to me."] if y),
        " ".join(y for y in [_skills_sentence(profile, detailed=True), proj] if y),
        task_details,
        programme_line or f"Would you consider me for {placement} in summer {year}? {profile.availability.strip()}."
        .replace("..", "."),
        humility or "",
        f"Thank you very much for considering this.{cv}",
        _signoff(profile),
    ] if x)

    # ---------------------------------------------------------------- networking message (≤ 300 chars)
    first = contact.get("name").split()[0] if contact and contact.get("name") else "there"
    hook = offerings[0][0] if offerings else focus
    net = (f"Hi {first}, I'm a {profile.age or ''}-year-old A-level student in {profile.location_city} interested in "
           f"{focus}. I came across {company}'s work on {hook} and would love to hear whether you ever host students "
           f"for summer {year} work experience. Thank you!").replace("a -year-old", "an")
    if len(net) > 300:
        net = (f"Hi {first}, I'm an A-level student in {profile.location_city} interested in {focus}. I'd love to "
               f"hear whether {company} ever hosts students for summer {year} work experience. Thank you!")
    net = net[:300]

    # ---------------------------------------------------------------- follow-up
    follow = "\n\n".join([
        greeting,
        f"I'm following up on my email from [date] about a short summer {year} placement at {company}. "
        f"I know you're busy, so I'll keep this brief.",
        f"I'm still very keen to learn about {focus} and to help with anything useful"
        + (f", such as {TASK_SHORT[top_tasks[0].template_key]}" if top_tasks else "") + ". "
        "If someone else would be better to contact, I'd be grateful for a pointer.",
        f"Thank you again for your time.{cv.replace('attached', 're-attached')}",
        _signoff(profile),
    ])

    # ---------------------------------------------------------------- introduction request
    target = ("someone in the engineering or operations team" if not small else "the founder or someone who leads "
              "the technical work")
    intro = "\n\n".join([
        "Hi [mutual contact's name],",
        "I hope you're well. I'm looking for a short work-experience placement in summer "
        f"{year} in {focus}, and {company} stands out to me because of {why_company}.",
        f"Would you be comfortable introducing me to {target} there? I'd keep it short and respectful of their time, "
        "and I'm happy to send a brief note you could forward.",
        "Thank you so much either way.",
        "Best wishes,\n" + _p(profile.full_name, "Your full name"),
    ])

    return [
        DraftOut(DraftKind.SHORT_EMAIL, subject, short_body, facts, warnings),
        DraftOut(DraftKind.DETAILED_EMAIL, subject, detailed_body, facts, warnings),
        DraftOut(DraftKind.NETWORKING, None, net, facts, warnings),
        DraftOut(DraftKind.FOLLOW_UP, f"Re: {subject}", follow, facts, warnings),
        DraftOut(DraftKind.INTRO_REQUEST, f"Could you introduce me to someone at {company}?", intro, facts, warnings),
    ]
