"""Deterministic natural-language → SearchCriteria parser.

Handles counts, locations (UAE gazetteer), industries (taxonomy synonyms),
exclusions, size words, internship type, skills/tasks the student wants to
offer, and the "engineering team" requirement. Anything it cannot interpret is
reported in ``unrecognised_terms`` and the UI asks the user to confirm.
"""

from __future__ import annotations

import re

from atlas.assess.taxonomy import INDUSTRIES
from atlas.geo.areas import AREAS, find_areas
from atlas.models.enums import BusinessType, Industry, SizeBand
from atlas.models.search import ParsedQuery, SearchCriteria

STOPWORDS = set("""a an the and or of in at on for to with without from by that which who where i me my we our you
your could would might may can will be is are was were it its this these those some any all into near based
find get list show give search look looking for companies company firms firm businesses business organisations
organizations employers employer prospects startups startup accept accepts intern interns internship internships
placement placements summer realistically help helping offer offering contribute related relevant like such as
etc also please uae dubai emirates teams team engineering technology tech local small medium large sized
mid size sme smes big who might want hire hiring take taking there their them they work working experience
student students school high other than not no except excluding exclude areas area region good great best
top most more less few many number around about approximately people""".split())

SKILL_TERMS = {
    "python": "python", "excel": "excel", "spreadsheet": "excel", "spreadsheets": "excel", "vba": "excel",
    "cad": "basic cad", "solidworks": "basic cad", "fusion 360": "basic cad", "autocad": "basic cad",
    "data analysis": "data analysis", "data analytics": "data analysis", "data cleaning": "data analysis",
    "research": "research", "documentation": "documentation", "technical writing": "documentation",
    "testing": "testing", "qa": "testing", "dashboards": "data analysis", "dashboard": "data analysis",
}
TASK_TERMS = {
    "supplier research": "supplier_research", "component research": "supplier_research",
    "competitor research": "competitor_research", "market research": "competitor_research",
    "excel automation": "spreadsheet_automation", "spreadsheet automation": "spreadsheet_automation",
    "python automation": "python_automation", "automation scripts": "python_automation",
    "scripting": "python_automation", "documentation": "technical_documentation",
    "cad support": "cad_support", "cad": "cad_support", "testing": "software_testing",
    "quality assurance": "qc_reporting", "data cleaning": "dataset_preparation", "reporting": "spreadsheet_automation",
    "dashboards": "dashboard_creation", "dashboard": "dashboard_creation", "bill of materials": "bom_organisation",
    "inventory": "inventory_data", "research": "research_summaries",
}
SKILL_CUE = re.compile(r"\b(where i (could|can|might) (help|assist|contribute|offer|support|use)( with| through| by)?|"
                       r"help(ing)? (them )?with|(could|can) offer|offer(ing)?|contribute (with|through|to)|"
                       r"assist(ing)? with|use my|using my|apply my)\b", re.IGNORECASE)
EXCLUDE_CUE = re.compile(r"\b(excluding|exclude|except|but not|not including|other than|without|no|not)\s+"
                         r"([a-z0-9 &/\-]+?)(?=[,.;]|\band\b|\bor\b|$)", re.IGNORECASE)
SIZE_WORDS: list[tuple[re.Pattern[str], list[SizeBand], str]] = [
    (re.compile(r"\b(startups?|start-ups?|early[- ]stage)\b", re.I), [SizeBand.MICRO, SizeBand.SMALL], "startup"),
    (re.compile(r"\b(small(er)?|tiny|boutique)\b", re.I), [SizeBand.MICRO, SizeBand.SMALL], "small"),
    (re.compile(r"\b(sme|smes|small and medium|small[- ]to[- ]medium|medium[- ]sized|mid[- ]sized|medium)\b", re.I),
     [SizeBand.SMALL, SizeBand.MEDIUM], "medium"),
    (re.compile(r"\b(large|big|major|multinational|multinationals|global|enterprise|corporates?)\b", re.I),
     [SizeBand.LARGE, SizeBand.ENTERPRISE], "large"),
]
BUSINESS_EXCLUSION_WORDS = {
    "recruitment": BusinessType.RECRUITMENT_AGENCY, "recruiters": BusinessType.RECRUITMENT_AGENCY,
    "agencies": BusinessType.RECRUITMENT_AGENCY, "staffing": BusinessType.RECRUITMENT_AGENCY,
    "training": BusinessType.TRAINING_PROVIDER, "training providers": BusinessType.TRAINING_PROVIDER,
    "distributors": BusinessType.DISTRIBUTOR_TRADING, "trading": BusinessType.DISTRIBUTOR_TRADING,
    "trading companies": BusinessType.DISTRIBUTOR_TRADING, "resellers": BusinessType.DISTRIBUTOR_TRADING,
    "consultancies": BusinessType.CONSULTANCY, "consultants": BusinessType.CONSULTANCY,
    "government": BusinessType.GOVERNMENT_PUBLIC, "retail": BusinessType.RETAIL_CONSUMER,
}

_SYNONYMS: list[tuple[str, Industry]] = sorted(
    ((syn, k) for k, d in INDUSTRIES.items() for syn in d.query_synonyms), key=lambda x: -len(x[0]))


def _find_industries(text: str) -> tuple[list[Industry], list[tuple[int, int]]]:
    found: list[Industry] = []
    spans: list[tuple[int, int]] = []
    low = text.lower()
    for syn, ind in _SYNONYMS:
        for m in re.finditer(r"(?<![a-z0-9])" + re.escape(syn) + r"(?![a-z0-9])", low):
            if any(s <= m.start() < e for s, e in spans):
                continue
            spans.append((m.start(), m.end()))
            if ind not in found:
                found.append(ind)
    return found, spans


def _industry_for_phrase(phrase: str) -> Industry | None:
    inds, _ = _find_industries(phrase)
    return inds[0] if inds else None


def parse_query(text: str, *, default_area: str = "dubai", default_count: int = 50) -> ParsedQuery:
    raw = " ".join((text or "").split())
    notes: list[str] = []
    work = raw.lower().replace("-related", " ").replace("–", " ").replace("—", " ")
    consumed: list[str] = []

    # --- count -----------------------------------------------------------
    count = None
    m = re.search(r"\b(?:find|get|list|show|give me|top|identify|discover)\s+(?:me\s+)?(\d{1,4})\b", work) or \
        re.search(r"\b(\d{1,4})\s+(?:[a-z&\-]+\s+){0,6}?(?:companies|firms|startups|businesses|organi[sz]ations|"
                  r"employers|prospects|places)\b", work)
    if m:
        count = max(1, min(1000, int(m.group(1))))
        consumed.append(m.group(1))
    else:
        notes.append(f"No target number given: using {default_count}.")

    # --- skills / tasks the student wants to offer --------------------------
    skills: list[str] = []
    tasks: list[str] = []
    cue = SKILL_CUE.search(work)
    if cue:
        clause = work[cue.end():]
        clause = re.split(r"[.;]|\bin (?=[a-z])", clause)[0]
        remaining = clause
        for term, canon in sorted(TASK_TERMS.items(), key=lambda x: -len(x[0])):
            pat = r"(?<![a-z])" + re.escape(term) + r"(?![a-z])"
            if re.search(pat, remaining):
                remaining = re.sub(pat, " ", remaining)  # longest match wins ("supplier research" ≠ "research")
                if canon not in tasks:
                    tasks.append(canon)
        for term, canon in sorted(SKILL_TERMS.items(), key=lambda x: -len(x[0])):
            if re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", clause) and canon not in skills:
                skills.append(canon)
        if skills or tasks:
            notes.append("Contribution focus: " + ", ".join(skills + [t.replace("_", " ") for t in tasks]))
            work = work[:cue.start()] + " " + work[cue.end() + len(clause):]

    # --- exclusions -------------------------------------------------------
    excluded_ind: list[Industry] = []
    excluded_kw: list[str] = []
    excluded_bt: list[BusinessType] = []
    excl_phrases: dict[Industry, list[str]] = {}
    for m in EXCLUDE_CUE.finditer(work):
        phrase = m.group(2).strip()
        if not phrase or phrase in {"more", "less", "one"}:
            continue
        bt = next((b for w, b in BUSINESS_EXCLUSION_WORDS.items() if w in phrase), None)
        ind = _industry_for_phrase(phrase)
        if bt is not None:
            excluded_bt.append(bt)
        elif ind is not None:
            excluded_ind.append(ind)
            excl_phrases.setdefault(ind, []).append(phrase[:40])
        else:
            excluded_kw.append(phrase[:40])
        work = work.replace(m.group(0), " ")

    # --- engineering-team requirement ------------------------------------
    require_eng = bool(re.search(r"\b(with|having|that have)\s+(an?\s+)?(in-house\s+)?engineering\s+teams?\b|"
                                 r"\bin-house engineers\b", work))
    if require_eng:
        work = re.sub(r"\b(with|having|that have)\s+(an?\s+)?(in-house\s+)?engineering\s+teams?\b|\bin-house engineers\b",
                      " ", work)
        notes.append("Requires evidence of an in-house engineering team.")

    # --- locations ----------------------------------------------------------
    areas = [a for a, _alias in find_areas(work)]
    # Prefer the most specific areas: drop an emirate/country if a zone inside it is named.
    keys = {a.key for a in areas}
    areas = [a for a in areas if not any((b.parent_key == a.key) for b in areas if b.key != a.key) or a.kind == "zone"]
    if not areas:
        areas = [AREAS[default_area]]
        notes.append(f"No location given: defaulted to {AREAS[default_area].label} (edit below).")
    for a in areas:
        for alias in a.aliases:
            work = re.sub(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", " ", work)
    _ = keys

    # --- industries -----------------------------------------------------------
    industries, spans = _find_industries(work)
    # An exclusion that hits a requested industry ("fintech, not crypto") narrows by keyword instead.
    for ind in [i for i in excluded_ind if i in industries]:
        excluded_ind.remove(ind)
        excluded_kw.extend(p for p in excl_phrases.get(ind, []) if p not in excluded_kw)
    if excluded_ind or excluded_kw or excluded_bt:
        notes.append("Exclusions: " + ", ".join([i.value for i in excluded_ind] + excluded_kw +
                                                 [b.value for b in excluded_bt]))
    if not industries and re.search(r"\bengineering\b", work):
        industries = [Industry.MECHANICAL, Industry.ROBOTICS, Industry.AEROSPACE]
        notes.append("'engineering' interpreted broadly as mechanical, robotics and aerospace (edit below).")
    if not industries and re.search(r"\b(tech|technology)\b", work):
        industries = [Industry.SOFTWARE, Industry.AI_ML, Industry.DATA_ANALYTICS]
        notes.append("'tech' interpreted as software, AI and data (edit below).")
    for s, e in sorted(spans, reverse=True):
        work = work[:s] + " " + work[e:]

    # --- size -------------------------------------------------------------------
    sizes: list[SizeBand] = []
    keywords: list[str] = []
    for pat, bands, label in SIZE_WORDS:
        if pat.search(raw):
            for b in bands:
                if b not in sizes:
                    sizes.append(b)
            if label == "startup":
                keywords.append("startup")
            work = pat.sub(" ", work)

    # --- internship type & quality --------------------------------------------
    itype = "summer"
    if re.search(r"\b(work experience|short placement|shadowing)\b", work):
        itype = "short_placement"
    elif re.search(r"\b(project|projects)\b", work):
        itype = "project"
    min_quality = 60.0 if re.search(r"\b(high[- ]quality|best|strong(est)? fit|top[- ]tier)\b", raw.lower()) else 0.0

    # --- leftovers ----------------------------------------------------------------
    leftovers = [w for w in re.findall(r"[a-z][a-z0-9&\-]{2,}", work)
                 if w not in STOPWORDS and w not in consumed and not w.isdigit()]
    # A word directly modifying a sector noun ("marine engineering", "medical devices companies") is a keyword.
    for w in list(dict.fromkeys(leftovers)):
        if re.search(rf"\b{re.escape(w)}\s+(engineering|technology|tech|companies|firms|industry|sector|"
                     rf"manufacturing|manufacturers|systems|startups|solutions|devices)\b", raw.lower()):
            keywords.append(w)
            leftovers = [x for x in leftovers if x != w]
            notes.append(f"Treated '{w}' as a keyword.")
    unrecognised = list(dict.fromkeys(leftovers))[:10]
    if unrecognised:
        notes.append("Not interpreted: " + ", ".join(unrecognised) + " — add them as keywords if they matter.")
    ambiguity = min(1.0, (0.5 if not industries else 0.0) + 0.1 * len(unrecognised))

    crit = SearchCriteria(
        raw_query=raw, areas=areas, industries=industries, excluded_industries=excluded_ind,
        keywords=keywords, excluded_keywords=excluded_kw,
        excluded_business_types=list(dict.fromkeys(SearchCriteria().excluded_business_types + excluded_bt)),
        size_preference=sizes, internship_type=itype, skills=skills, task_preferences=tasks,
        desired_count=count or default_count, min_quality=min_quality, require_engineering_team=require_eng,
    )
    if not industries:
        notes.append("No industry recognised — all target industries will be considered.")
    return ParsedQuery(criteria=crit, method="deterministic", notes=notes, unrecognised_terms=unrecognised,
                       ambiguity=round(ambiguity, 2))
