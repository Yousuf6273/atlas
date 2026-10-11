"""Task-opportunity intelligence.

Each template describes supervised, office-based support work a school student
could realistically do. A template is proposed for a company only when the
company's *own published text* triggers it — industry alone is never enough.

* Observed need: the company is publicly recruiting for a matching function
  (e.g. a "Document Controller" or "Data Analyst" vacancy) or explicitly asks
  for help. Evidence: the vacancy title / statement.
* Inferred opportunity: a hypothesis from what the company says it does
  (e.g. it manufactures assemblies → it has bills of materials). Always worded
  as a possibility, never as a known internal problem.

Triggers are *specific* (e.g. "bill of materials", "SolidWorks") or *weak*
(generic words such as "data" or "platform", marked ``~``). A template needs at
least one specific trigger or an observed need; weak triggers only add
supporting weight (0.5 each).

Confidence rubric
-----------------
high   – observed need, or ≥ 3 trigger points across ≥ 2 pages
medium – ≥ 2 trigger points
low    – fewer
Tasks whose required skills the student lacks (skill match < 0.5) are dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from atlas.assess.dossier import Dossier
from atlas.models.assessment import QualificationResult, TaskOpportunity
from atlas.models.company import Evidence
from atlas.models.enums import Level, Supervision, TaskBasis
from atlas.models.profile import StudentProfile

# Canonical skill names → synonyms accepted from the profile.
SKILL_SYNONYMS: dict[str, set[str]] = {
    "python": {"python", "python scripting", "python automation", "programming", "coding"},
    "excel": {"excel", "spreadsheets", "excel automation", "google sheets", "vba"},
    "research": {"research", "market research", "supplier research", "desk research"},
    "documentation": {"documentation", "technical writing", "writing", "engineering documentation"},
    "basic cad": {"basic cad", "cad", "solidworks", "fusion 360", "autocad", "onshape", "inventor", "cad support"},
    "data analysis": {"data analysis", "data analytics", "statistics", "data cleaning", "pandas", "analysis"},
    "testing": {"testing", "qa", "quality assurance", "software testing", "test"},
}


def canonical_skills(profile: StudentProfile) -> set[str]:
    have = profile.normalized_skills()
    out = set()
    for canon, syns in SKILL_SYNONYMS.items():
        if have & syns:
            out.add(canon)
    return out


@dataclass(frozen=True)
class TaskTemplate:
    key: str
    title: str
    description: str
    skills: tuple[str, ...]
    difficulty: Level
    supervision: Supervision
    triggers: tuple[str, ...]
    observed_roles: tuple[str, ...] = ()
    angle: str = ""
    safety_note: str | None = None
    compiled: list[tuple[re.Pattern[str], bool]] = field(default_factory=list, compare=False, hash=False)  # (pattern, weak)
    compiled_roles: list[re.Pattern[str]] = field(default_factory=list, compare=False, hash=False)


def _t(key, title, description, skills, difficulty, supervision, triggers, observed_roles=(), angle="", safety=None):
    return TaskTemplate(
        key, title, description, tuple(skills), difficulty, supervision, tuple(triggers), tuple(observed_roles), angle,
        safety,
        [(re.compile(r"(?<![A-Za-z])(?:" + t.lstrip("~") + r")(?![A-Za-z])", re.IGNORECASE), t.startswith("~"))
         for t in triggers],
        [re.compile(r"(?<![A-Za-z])(?:" + r + r")(?![A-Za-z])", re.IGNORECASE) for r in observed_roles],
    )


TEMPLATES: list[TaskTemplate] = [
    _t("supplier_research", "Supplier and component comparison research",
       "Compile a structured comparison of suppliers or components (specs, lead times, prices, certifications) "
       "from public datasheets, for an engineer to review.",
       ["research", "excel"], Level.LOW, Supervision.RECOMMENDED,
       [r"procurement", r"sourcing", r"~suppliers?", r"~vendors?", r"~components?", r"spare parts", r"raw materials?",
        r"supply chain", r"bill of materials", r"\bBOM\b"],
       [r"procurement (assistant|officer|executive)", r"buyer", r"purchasing (assistant|officer)", r"sourcing"],
       "offer to put together a supplier/component comparison sheet on a topic they choose"),
    _t("bom_organisation", "Bill of materials and parts-list organisation",
       "Clean up and standardise parts lists or bills of materials in a spreadsheet (part numbers, quantities, "
       "descriptions), flagging inconsistencies for the team.",
       ["excel", "data analysis"], Level.MEDIUM, Supervision.RECOMMENDED,
       [r"~manufactur\w*", r"assembl(y|ies)", r"~fabricat\w*", r"machining", r"\bCNC\b", r"bill of materials",
        r"\bBOM\b", r"parts? (list|catalogue|numbers?)", r"~product range"],
       [r"document controller", r"inventory (controller|analyst)", r"planner"],
       "offer to help tidy and standardise a parts list or BOM spreadsheet"),
    _t("spreadsheet_automation", "Spreadsheet and report automation",
       "Automate a repetitive spreadsheet or reporting task (formulas, templates, or a small Python script) "
       "that someone currently does by hand.",
       ["excel", "python"], Level.MEDIUM, Supervision.RECOMMENDED,
       [r"~reports?", r"~reporting", r"quotations?", r"estimat(ion|ing)", r"tender(s|ing)?", r"project tracking",
        r"\bKPIs?\b", r"spreadsheets?", r"data entry", r"~invoic\w+", r"~schedul(e|es|ing)"],
       [r"data entry", r"admin(istrative)? assistant", r"(mis|reporting) (analyst|executive)", r"estimator"],
       "offer to automate one repetitive spreadsheet or reporting task"),
    _t("qc_reporting", "Quality-control data logging and reporting",
       "Organise inspection or test results into a consistent log and simple summary charts so trends are easier "
       "to see.",
       ["excel", "data analysis"], Level.MEDIUM, Supervision.REQUIRED,
       [r"ISO 9001", r"quality control", r"quality assurance", r"QA/QC", r"\bQA\b", r"\bQC\b", r"inspections?",
        r"calibration", r"\bNDT\b", r"non-destructive testing", r"test reports?", r"testing (lab|laboratory)"],
       [r"quality (inspector|engineer|controller|officer)", r"qa/qc", r"\bQC\b inspector"],
       "offer to help organise QC/inspection data into a clear log and summary",
       "Data handling only — no hands-on inspection or testing of equipment."),
    _t("technical_documentation", "Technical documentation and datasheet organisation",
       "Organise product datasheets, manuals or specification sheets into a consistent, searchable library, or "
       "draft simple internal how-to notes for review.",
       ["documentation", "research"], Level.LOW, Supervision.MINIMAL,
       [r"datasheets?", r"technical specifications?", r"~specifications", r"manuals?", r"catalogu?es?",
        r"~documentation", r"user guides?", r"~certifications?", r"~brochures?", r"compliance documents?"],
       [r"document controller", r"technical writer", r"documentation (specialist|engineer)"],
       "offer to organise technical documents or datasheets"),
    _t("cad_support", "Basic CAD documentation support (supervised)",
       "Help with simple CAD tasks under an engineer's direction — e.g. tidying drawing title blocks, converting "
       "sketches into basic 3D models, or organising a drawing register. Never signing off designs.",
       ["basic cad"], Level.MEDIUM, Supervision.REQUIRED,
       [r"\bCAD\b", r"SolidWorks", r"AutoCAD", r"Fusion 360", r"Inventor", r"CATIA", r"Creo", r"Revit",
        r"3D model(l)?ing", r"shop drawings?", r"design engineering", r"product design", r"~drawings?"],
       [r"draughts?man", r"drafter", r"cad (technician|designer|engineer|operator)", r"design engineer"],
       "offer basic, supervised CAD/drawing-register support",
       "No approval or sign-off of engineering drawings."),
    _t("python_automation", "Python scripting for internal data tasks",
       "Write small, reviewed Python scripts to clean, merge or check data files, or to automate a routine "
       "export/import.",
       ["python"], Level.MEDIUM, Supervision.RECOMMENDED,
       [r"~automation", r"~data", r"~software", r"~platform", r"\bAPIs?\b", r"~digital transformation", r"~analytics",
        r"python", r"scripts?", r"~dashboards?"],
       [r"python developer", r"automation engineer", r"data engineer", r"software engineer"],
       "offer a small Python script to automate a data chore"),
    _t("software_testing", "Product testing and bug documentation",
       "Test a web or mobile product against simple test cases and write clear, reproducible bug reports.",
       ["testing", "documentation"], Level.LOW, Supervision.RECOMMENDED,
       [r"~\bapps?\b", r"mobile app(lication)?s?", r"web app(lication)?s?", r"\bSaaS\b", r"~platform", r"beta",
        r"~release", r"~user experience", r"\bUX\b", r"software products?"],
       [r"qa (engineer|tester|analyst)", r"test(er| engineer)", r"quality assurance (engineer|analyst)"],
       "offer to do structured testing and write up bug reports"),
    _t("dataset_preparation", "Dataset cleaning and labelling support",
       "Help clean, label or document non-sensitive datasets used for models or analysis, following the team's "
       "guidelines.",
       ["python", "data analysis"], Level.MEDIUM, Supervision.RECOMMENDED,
       [r"machine learning", r"computer vision", r"AI models?", r"training data", r"annotation", r"datasets?",
        r"\bNLP\b", r"deep learning", r"data science", r"labell?ing"],
       [r"data (annotator|labell?er|analyst|scientist)", r"ml engineer", r"machine learning engineer"],
       "offer help with dataset cleaning or labelling",
       "Only non-sensitive data; no access to personal or client data."),
    _t("competitor_research", "Competitor and market landscape research",
       "Produce a short, sourced overview of competitors, products or market segments the team is interested in.",
       ["research", "excel"], Level.LOW, Supervision.MINIMAL,
       [r"start-?up", r"~launch(ed|ing)?", r"~expan(d|ding|sion)", r"new markets?", r"~\bGCC\b", r"competitors?",
        r"~market leader", r"investors?", r"Series [A-C]", r"seed (round|funding)", r"~growth"],
       [r"market research (analyst|executive)", r"business analyst", r"strategy analyst"],
       "offer a short, sourced competitor or market overview"),
    _t("inventory_data", "Inventory and stock data organisation",
       "Reconcile and tidy stock or spare-parts records in a spreadsheet and flag duplicates or gaps.",
       ["excel"], Level.LOW, Supervision.RECOMMENDED,
       [r"warehouse", r"inventory", r"~stock", r"spare parts", r"~logistics", r"~distribution"],
       [r"storekeeper", r"inventory (controller|clerk|analyst)", r"warehouse (assistant|coordinator)"],
       "offer to help tidy inventory or spare-parts records"),
    _t("dashboard_creation", "KPI dashboard prototype",
       "Build a simple dashboard (Excel or Python) that summarises a dataset the team already collects.",
       ["excel", "python", "data analysis"], Level.MEDIUM, Supervision.RECOMMENDED,
       [r"~analytics", r"business intelligence", r"\bKPIs?\b", r"performance monitoring", r"~data-driven",
        r"dashboards?", r"Power BI", r"Tableau", r"~reporting"],
       [r"(bi|data|reporting) analyst", r"business intelligence"],
       "offer to prototype a simple KPI dashboard"),
    _t("robotics_test_logging", "Robot/automation test logging and component research",
       "Record and organise test runs, sensor logs or component options for a robotics/automation project under "
       "supervision.",
       ["python", "research", "documentation"], Level.MEDIUM, Supervision.REQUIRED,
       [r"robot(s|ic|ics)?", r"\bPLCs?\b", r"industrial automation", r"~sensors?", r"actuators?", r"cobots?", r"\bAGVs?\b",
        r"\bAMRs?\b", r"mechatronics", r"control systems?", r"SCADA"],
       [r"robotics (engineer|technician)", r"automation (engineer|technician)", r"controls engineer"],
       "offer supervised help with test logging or component research",
       "No operation of robots or machinery; lab work only under direct supervision."),
    _t("flight_data_analysis", "Flight/telemetry log analysis",
       "Clean and plot flight-test or telemetry logs and summarise basic statistics for an engineer.",
       ["python", "data analysis"], Level.MEDIUM, Supervision.REQUIRED,
       [r"\bUAVs?\b", r"drones?", r"telemetry", r"flight tests?", r"avionics", r"flight data", r"unmanned"],
       [r"flight test engineer", r"uav (engineer|pilot|technician)", r"avionics engineer"],
       "offer to help analyse flight or telemetry logs",
       "Data analysis only — no involvement in flight operations."),
    _t("maintenance_records", "Maintenance-record digitisation and analysis",
       "Digitise and analyse maintenance or service records (intervals, recurring faults) in a spreadsheet.",
       ["excel", "data analysis"], Level.LOW, Supervision.RECOMMENDED,
       [r"\bMRO\b", r"~maintenance", r"preventive maintenance", r"service records?", r"\bAMC\b", r"~asset management",
        r"overhaul", r"~repairs?"],
       [r"maintenance planner", r"planning engineer", r"records (clerk|controller)"],
       "offer to digitise or analyse maintenance records",
       "Records only — no hands-on maintenance."),
    _t("financial_data_analysis", "Market data cleaning and simple backtesting support",
       "Clean market or financial datasets and run simple, clearly documented analyses for an analyst to review.",
       ["python", "data analysis"], Level.MEDIUM, Supervision.REQUIRED,
       [r"~trading", r"quantitative", r"investment research", r"~portfolio", r"~asset management", r"market data",
        r"risk (analytics|management|models?)", r"hedge funds?", r"backtest\w*", r"systematic"],
       [r"quant(itative)? (analyst|researcher|developer)", r"investment analyst", r"research analyst", r"data analyst"],
       "offer help cleaning market data or documenting a simple analysis",
       "No client money, trading decisions or regulated activity."),
    _t("research_summaries", "Technical or regulatory research summaries",
       "Summarise public standards, regulations or technical literature on a topic the team specifies, with "
       "sources.",
       ["research", "documentation"], Level.LOW, Supervision.MINIMAL,
       [r"regulations?", r"~standards?", r"compliance", r"~certifications?", r"GCAA", r"EASA", r"\bISO\b",
        r"~sustainability", r"~research"],
       [r"compliance (officer|analyst)", r"research (assistant|analyst)"],
       "offer short, sourced research summaries on a topic they choose"),
]


def _sentence(text: str, start: int, end: int) -> str:
    left = max(text.rfind(". ", 0, start), text.rfind("\n", 0, start))
    right = [i for i in (text.find(". ", end), text.find("\n", end)) if i != -1]
    s = text[left + 1: (min(right) + 1) if right else len(text)].strip()
    return " ".join(s.split())[:300]


def _confidence(observed: bool, distinct_terms: float, pages: int) -> Level:
    if observed or (distinct_terms >= 3 and pages >= 2):
        return Level.HIGH
    if distinct_terms >= 2:
        return Level.MEDIUM
    return Level.LOW


def find_tasks(d: Dossier, q: QualificationResult, profile: StudentProfile, *, max_tasks: int = 6) -> list[TaskOpportunity]:
    student = canonical_skills(profile)
    pages = [p for p in d.good_pages if p.page_type not in ("news",)]
    roles = d.claims_of("hiring_role")
    out: list[tuple[float, TaskOpportunity]] = []
    for tpl in TEMPLATES:
        needed = set(tpl.skills)
        skill_match = len(needed & student) / len(needed) if needed else 1.0
        if skill_match < 0.5:
            continue
        terms: dict[str, Evidence] = {}
        weak_terms: set[str] = set()
        pages_hit: set[str] = set()
        for p in pages:
            for pat, is_weak in tpl.compiled:
                m = pat.search(p.text)
                if m:
                    term = m.group(0).lower()
                    if term not in terms:
                        terms[term] = Evidence(source_url=p.url, snippet=_sentence(p.text, m.start(), m.end()),
                                               retrieved_at=p.fetched_at, webpage_id=p.id)
                        if is_weak:
                            weak_terms.add(term)
                    pages_hit.add(p.url)
        observed_ev: list[Evidence] = []
        for r in roles:
            if any(rp.search(r["value"]) for rp in tpl.compiled_roles):
                observed_ev.append(Evidence(source_url=r["source_url"], snippet=f"Vacancy listed: {r['value']}",
                                            retrieved_at=r["retrieved_at"]))
        specific = [t for t in terms if t not in weak_terms]
        if not specific and not observed_ev:
            continue  # generic words alone ("data", "platform") are not evidence
        observed = bool(observed_ev)
        score_terms = len(specific) + 0.5 * len(weak_terms)
        conf = _confidence(observed, score_terms, len(pages_hit))
        ordered = specific + [t for t in terms if t in weak_terms]
        evidence = observed_ev[:2] + [terms[t] for t in ordered[:3]]
        term_list = ", ".join(ordered[:4])
        if observed:
            rationale = (f"The company is publicly recruiting for a related role ({observed_ev[0].snippet[16:]}), "
                         f"which indicates this function exists in the team. A student could support a small, "
                         f"well-defined part of it.")
        else:
            rationale = (f"Inferred from the company's own website, which mentions {term_list}. This is a "
                         f"possibility to offer, not a known need.")
        task = TaskOpportunity(
            template_key=tpl.key, title=tpl.title, description=tpl.description, rationale=rationale,
            basis=TaskBasis.OBSERVED if observed else TaskBasis.INFERRED, evidence=evidence,
            skills=[s.title() if s != "basic cad" else "Basic CAD" for s in tpl.skills], difficulty=tpl.difficulty,
            supervision=tpl.supervision, confidence=conf, skill_match=round(skill_match, 2),
            outreach_angle=tpl.angle, safety_note=tpl.safety_note,
        )
        weight = {Level.HIGH: 3, Level.MEDIUM: 2, Level.LOW: 1}[conf] + (1.5 if observed else 0) + skill_match
        out.append((weight, task))
    out.sort(key=lambda x: -x[0])
    return [t for _w, t in out[:max_tasks]]
