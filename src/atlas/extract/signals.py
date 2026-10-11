"""Evidence-linked signal extraction from parsed pages.

Every claim produced here quotes the sentence it came from and the URL of the
page, so it can be checked by a human. Extractors are deterministic rules;
claims are facts *about what the page says*, not inferences.
"""

from __future__ import annotations

import re
from datetime import date

from atlas.assess.relevance import scan
from atlas.assess.taxonomy import compiled_business_types
from atlas.crawl.html import ParsedPage, sentences
from atlas.geo.areas import find_areas
from atlas.llm.guard import INJECTION_PATTERNS
from atlas.models.company import Claim, Evidence

# ------------------------------------------------------------------ patterns

def _c(p: str, flags=re.IGNORECASE) -> re.Pattern[str]:
    return re.compile(p, flags)


INTERNSHIP = _c(r"\b(internships?|interns(?!hip)\b|intern\b(?! ?ational)|graduate (trainee|program(me)?|scheme)s?|"
                r"work experience (program(me)?s?|placements?|opportunit(y|ies)|requests?|for (school )?students|weeks?)|summer (program(me)?|placements?|"
                r"internships?)|student (placements?|program(me)?s?|opportunit(y|ies))|traineeships?|"
                r"apprenticeships?|early careers?)\b")
SCHOOL_OK = _c(r"\b((high|secondary)[- ]school (students?|pupils?)|school students|a[- ]levels?|ib (diploma )?students|"
               r"grade (11|12)|year (12|13) (students?|pupils?)|work experience for (students|young people)|"
               r"students aged (15|16|17)|aged (15|16|17)( or| and| to)|16[- ]18[- ]year[- ]olds?)\b")
UNIVERSITY_ONLY = _c(r"\b((currently )?(enrolled|studying) (in|at) (a |an )?(university|college|bachelor'?s?|master'?s?|"
                     r"degree|undergraduate)|(undergraduate|university|college) students?( only)?|final[- ]year students?|"
                     r"pursuing (a|an|your) (bachelor|master|degree|engineering degree)|"
                     r"must (hold|have) (a |an )?(bachelor|degree)|bachelor'?s degree (in|required)|minimum gpa|gpa of)\b")
MIN_AGE = [
    _c(r"\b(?:minimum|min\.?) age(?: of| is|:)?\s*(\d{2})\b"),
    _c(r"\bmust be (?:at least )?(\d{2})(?: years)?(?: (?:old|of age))?\b"),
    _c(r"\b(\d{2}) years (?:of age )?(?:or|and) (?:older|above|over)\b"),
    _c(r"\b(?:aged|age) (\d{2})\s*(?:\+|or (?:older|above))"),
    _c(r"\bover the age of (\d{2})\b"),
]
RESTRICTED = _c(r"\b(security clearance|classified (projects|information)|airside|explosives?|ammunition|weapons? systems?|"
                r"nuclear|itar|export[- ]controlled|hazardous (materials|areas)|confined spaces?|offshore (rigs?|platforms?)|"
                r"h2s|military (programs?|grade|projects?)|defen[cs]e (programs?|contracts?|systems?|sector|industry))\b")
HIRING = _c(r"\b(we('| a)re hiring|join our team|current (openings|vacancies)|open (positions|roles)|"
            r"job openings|apply now|vacancies|view (all )?jobs|career opportunities)\b")
ROLE_NOUN = _c(r"\b(engineer|technician|analyst|developer|manager|intern|specialist|coordinator|designer|draughts?man|"
               r"drafter|controller|officer|assistant|executive|scientist|programmer|tester|architect|consultant|"
               r"supervisor|estimator|planner|buyer|administrator|operator|machinist|welder|accountant)s?\b")
ENGINEERING_TEAM = _c(r"\b((our|in-house|dedicated|experienced|skilled|qualified|expert) (team of )?(engineers|engineering team|"
                      r"r&d team|developers|data scientists|designers)|team of \d[\d,]*\+? (engineers|developers|"
                      r"specialists|scientists|professionals)|engineering (department|division|team|centre|center)|"
                      r"r&d (centre|center|lab|laboratory|facility|team|department)|design (office|team))\b")
SIZE = _c(r"\b(?:(over|more than|approximately|around|nearly|about|almost|upwards of)\s+)?(\d{1,3}(?:,\d{3})+|\d{1,6})\s*(\+)?\s*"
          r"(employees|staff|people|professionals|engineers|team members|workforce|personnel|colleagues|experts)\b")
SIZE_ALT = _c(r"\b(workforce|team) of (?:over |more than |around |approximately )?(\d{1,3}(?:,\d{3})+|\d{1,6})\s*(\+)?")
COPYRIGHT = _c(r"(?:©|&copy;|\(c\)|copyright)\s*(?:\w+\s+){0,3}?(\d{4})(?:\s*[-–]\s*(\d{4}))?")
DATE_PATTERNS = [
    _c(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+(20\d{2})\b"),
    _c(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b"),
    _c(r"\b(20\d{2})-(\d{2})-(\d{2})\b"),
]
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"], 1)} | {"sept": 9}
ACTIVITY_VERB = _c(r"\b(we|our (company|team|engineers)|the company)\b.{0,60}\b(design|develop|manufactur|provid|offer|"
                   r"deliver|speciali[sz]|build|engineer|supply|produc|operat|serv|help|creat|integrat|install|"
                   r"maintain|research|focus)\w*")
BOILERPLATE = _c(r"\b(cookies?|privacy policy|terms (of use|and conditions)|gdpr|all rights reserved)\b")
REQUIREMENT_CONTEXT = _c(r"\b(must|required|requirements?|only|open to|eligib\w*|should be|need to be|"
                         r"applicants?|candidates?|criteria|qualifications?)\b")
HIRING_CONTEXT = _c(r"\b(intern\w*|applicants?|candidates?|apply|application|positions?|roles?|jobs?|employment|"
                    r"vacanc\w+|placements?|work experience|recruit\w*)\b")
FOREIGN_PLACE = _c(r"\b(india|mumbai|thane|navi mumbai|bangalore|bengaluru|pune|delhi|hyderabad|chennai|kolkata|"
                   r"pakistan|karachi|lahore|egypt|cairo|london|united kingdom|usa|united states|canada|"
                   r"philippines|manila|kenya|nigeria|lebanon|beirut|jordan|amman)\b")
UAE_SIGNAL = _c(r"(\+971|\b00971\b|\bu\.?a\.?e\.?\b|united arab emirates|p\.?\s?o\.?\s?box\s*\d+)")
# Marketing slogans are not names of products/services ("We make AI easy", "Your growth system").
SLOGAN = _c(r"(\b(we|our|ours|your|you|us|i|my|they|let'?s)\b|[?!]|^(get|make|build|grow|discover|transform|unlock|boost|"
            r"scale|start|join|meet|see|why|how|what|when|where|who|explore|learn|empower|drive|bring|take|turn|"
            r"power|creating|building|making|delivering|driving|helping)\b)")
GENERIC_HEADINGS = _c(r"^(home|about( us)?|contact( us)?|our (services|products|solutions|clients|team|projects)|services|"
                      r"products|solutions|read more|learn more|get in touch|why choose us|testimonials?|faq|"
                      r"news|blog|careers?|follow us|quick links|useful links|newsletter|subscribe|menu|search|"
                      r"welcome( to .*)?|who we are|what we do|latest news|our partners|partners|clients|"
                      r"copyright.*|all rights reserved.*|view all|see more|explore|discover more)$")


def _ev(page: ParsedPage, snippet: str, retrieved_at: str) -> Evidence:
    return Evidence(source_url=page.url, snippet=" ".join(snippet.split())[:400], retrieved_at=retrieved_at)


def _sentence_with(text_sents: list[str], start_text: str) -> str:
    for s in text_sents:
        if start_text.lower() in s.lower():
            return s
    return start_text


def _strip_injected(full: str) -> tuple[str, list[str]]:
    """Remove sentences that address automated readers ("ignore previous instructions…").

    Such text is an instruction to machines, not a statement of fact about the
    company, so no claim may be derived from it. The removed text is reported.
    """
    removed: list[str] = []
    kept_blocks = []
    for block in full.split("\n"):
        parts = re.split(r"(?<=[.!?])\s+", block)
        good = []
        for part in parts:
            if INJECTION_PATTERNS.search(part):
                removed.append(part.strip()[:300])
            else:
                good.append(part)
        kept_blocks.append(" ".join(good))
    return "\n".join(kept_blocks), removed


def extract_claims(page: ParsedPage, page_type: str, retrieved_at: str, *, company_name: str = "",
                   today: date | None = None) -> list[Claim]:
    today = today or date.today()
    claims: list[Claim] = []
    full, injected = _strip_injected(page.full_text)
    text = _strip_injected(page.text)[0]
    sents = sentences(full)
    for inj in injected[:3]:
        claims.append(Claim(claim_type="prompt_injection_text", value="instruction-like text ignored",
                            evidence=_ev(page, inj, retrieved_at), extractor="rule:injection_filter", confidence=0.9))

    def add(ctype: str, value: str, snippet: str, extractor: str, conf: float, inference: bool = False) -> None:
        claims.append(Claim(claim_type=ctype, value=value[:500], evidence=_ev(page, snippet, retrieved_at),
                            extractor=f"rule:{extractor}", confidence=conf, is_inference=inference))

    # --- what the company does (activity statements) ---------------------
    if page_type in ("home", "about", "services", "products", "industries", "other", "projects"):
        if page.meta_description and page_type in ("home", "about"):
            add("activity", page.meta_description, page.meta_description, "meta_description", 0.7)
        n = 0
        for s in sents:
            if n >= 6:
                break
            if not (40 <= len(s) <= 320):
                continue
            mentions_company = company_name and company_name.split()[0].lower() in s.lower()
            if (ACTIVITY_VERB.search(s) or mentions_company) and any(h.strength != "negative" for h in scan(s)):
                add("activity", s, s, "activity_sentence", 0.75 if page_type in ("about", "home") else 0.7)
                n += 1

    # --- offerings: short headings on service/product pages --------------
    if page_type in ("services", "products", "home", "industries", "projects"):
        k = 0
        for tag, h in page.headings:
            words = h.split()
            if tag in ("h2", "h3", "h4") and 1 <= len(words) <= 6 and not GENERIC_HEADINGS.match(h.strip().lower()) \
                    and not SLOGAN.search(h) and any(h2.strength != "negative" for h2 in scan(h)):
                add("offering", h, _sentence_with(sents, h), "heading", 0.7)
                k += 1
                if k >= 8:
                    break

    # --- industry and business-type signals (first sentence per term) ----
    seen_terms: set[str] = set()
    for hit in scan(full):
        key = f"{hit.industry.value}:{hit.strength}:{hit.term.lower()}"
        if key in seen_terms:
            continue
        seen_terms.add(key)
        snippet = _window_sentence(full, hit.start, hit.end)
        ctype = "industry_negative" if hit.strength == "negative" else "industry_signal"
        add(ctype, f"{hit.industry.value}|{hit.strength}|{hit.term.lower()}", snippet, "taxonomy",
            0.8 if hit.strength == "strong" else 0.5)
    # Cookie / privacy / terms boilerplate says nothing about what the company does.
    bt_text = "\n".join(s for s in full.split("\n") if not BOILERPLATE.search(s))
    for bdef, pats, _names in compiled_business_types():
        found = 0
        for pat, weak in pats:
            m = pat.search(bt_text)
            if m:
                term = ("~" if weak else "") + m.group(0).lower()
                add("business_type_signal", f"{bdef.key.value}|{term}",
                    _window_sentence(bt_text, m.start(), m.end()), "business_type", 0.4 if weak else 0.7)
                found += 1
                if found >= 3:
                    break

    # --- internships, eligibility, restrictions ---------------------------
    for m in INTERNSHIP.finditer(full):
        snippet = _window_sentence(full, m.start(), m.end())
        context = f"{page.title} {snippet}"
        if FOREIGN_PLACE.search(context) and not UAE_SIGNAL.search(context):
            # e.g. "Python Developer Internship in Thane, India" — not a UAE opportunity.
            add("internship_abroad", m.group(0).lower(), snippet, "internship", 0.6)
        else:
            add("internship_signal", m.group(0).lower(), snippet, "internship",
                0.8 if page_type in ("internship", "careers") else 0.55)
        break
    for m in SCHOOL_OK.finditer(full):
        snippet = _window_sentence(full, m.start(), m.end())
        # Must be about the company taking students on (not, e.g., a news story about a school competition).
        if page_type in ("careers", "internship") or INTERNSHIP.search(snippet) or HIRING_CONTEXT.search(snippet):
            add("eligibility_school_ok", m.group(0).lower(), snippet, "eligibility", 0.8)
            break
    for m in UNIVERSITY_ONLY.finditer(full):
        snippet = _window_sentence(full, m.start(), m.end())
        # Must read as a requirement ("open to undergraduates", "must be enrolled"), not an audience
        # ("whether you are a professional or a student").
        if (INTERNSHIP.search(snippet) or page_type in ("internship", "careers")) and REQUIREMENT_CONTEXT.search(snippet):
            add("eligibility_university_only", m.group(0).lower(), snippet, "eligibility", 0.75)
            break
    ages_seen: set[int] = set()
    for pat in MIN_AGE:
        for m in pat.finditer(full):
            age = int(m.group(1))
            snippet = _window_sentence(full, m.start(), m.end())
            # Ignore site terms of use ("you must be 18 to use this website").
            if page_type not in ("careers", "internship") and not HIRING_CONTEXT.search(snippet):
                continue
            if 14 <= age <= 25 and age not in ages_seen:
                ages_seen.add(age)
                add("eligibility_min_age", str(age), _window_sentence(full, m.start(), m.end()), "eligibility", 0.8)
    for m in RESTRICTED.finditer(full):
        add("restricted_environment", m.group(0).lower(), _window_sentence(full, m.start(), m.end()), "restricted", 0.6)
        break

    # --- hiring ----------------------------------------------------------
    if page_type in ("careers", "internship"):
        add("hiring", "careers page", page.title or page.url, "careers_page", 0.9)
        r = 0
        for tag, h in page.headings + [("li", b) for b in page.blocks if len(b) < 90]:
            if 2 <= len(h.split()) <= 10 and ROLE_NOUN.search(h) and not GENERIC_HEADINGS.match(h.lower()):
                add("hiring_role", h, h, "vacancy_title", 0.6)
                r += 1
                if r >= 10:
                    break
    m = HIRING.search(full)
    if m:
        add("hiring", m.group(0).lower(), _window_sentence(full, m.start(), m.end()), "hiring_phrase", 0.6)

    # --- engineering team, size -------------------------------------------
    m = ENGINEERING_TEAM.search(full)
    if m:
        add("engineering_team", m.group(0).lower(), _window_sentence(full, m.start(), m.end()), "engineering_team", 0.75)
    for m in list(SIZE.finditer(full))[:3] + list(SIZE_ALT.finditer(full))[:2]:
        groups = [g for g in m.groups() if g]
        num = next((g for g in groups if re.fullmatch(r"\d{1,3}(?:,\d{3})+|\d{1,6}", g)), None)
        if not num:
            continue
        n_val = int(num.replace(",", ""))
        if 1900 <= n_val <= 2100 or n_val < 2:
            continue
        snippet = _window_sentence(full, m.start(), m.end())
        if re.search(r"\b(clients|customers|projects|countries|years|users|students|visitors|downloads)\b",
                     m.group(0), re.I):
            continue
        add("size_statement", f"{m.group(0).strip()}|{n_val}", snippet, "size", 0.6)

    # --- location ---------------------------------------------------------
    for area, alias in find_areas(full)[:4]:
        idx = full.lower().find(alias)
        add("location_mention", area.key, _window_sentence(full, idx, idx + len(alias)) if idx >= 0 else alias,
            "gazetteer", 0.6)
    m = UAE_SIGNAL.search(full)
    if m:
        add("uae_presence", m.group(0), _window_sentence(full, m.start(), m.end()), "uae_signal", 0.6)

    # --- recency (copyright, dated news) -----------------------------------
    years = []
    for m in COPYRIGHT.finditer(page.footer_text + "\n" + text[-3000:]):
        for g in m.groups():
            if g and 1990 <= int(g) <= today.year + 1:
                years.append(int(g))
    if years:
        add("copyright_year", str(max(years)), f"© {max(years)} (page footer)", "copyright", 0.5)
    if page_type in ("news", "home", "projects"):
        latest = _latest_date(full, today)
        if latest:
            add("dated_content", latest.isoformat(), f"dated content on page: {latest.isoformat()}", "dates", 0.5)
    return claims


def _window_sentence(text: str, start: int, end: int) -> str:
    """The sentence (bounded by . ! ? or newline) around [start, end)."""
    left = max(text.rfind(". ", 0, start), text.rfind("\n", 0, start), text.rfind("! ", 0, start),
               text.rfind("? ", 0, start))
    right_candidates = [i for i in (text.find(". ", end), text.find("\n", end), text.find("! ", end),
                                    text.find("? ", end)) if i != -1]
    right = min(right_candidates) if right_candidates else len(text)
    s = text[left + 1: right + 1].strip()
    if len(s) > 350:
        s = text[max(0, start - 160): min(len(text), end + 160)].strip()
    return " ".join(s.split())


def _latest_date(text: str, today: date) -> date | None:
    found: list[date] = []
    for i, pat in enumerate(DATE_PATTERNS):
        for m in pat.finditer(text):
            try:
                if i == 0:
                    d = date(int(m.group(3)), MONTHS[m.group(2).lower()[:3]], int(m.group(1)))
                elif i == 1:
                    d = date(int(m.group(3)), MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
                else:
                    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except (ValueError, KeyError):
                continue
            if date(2000, 1, 1) <= d <= today:
                found.append(d)
    return max(found) if found else None


def extract_structured(page: ParsedPage, retrieved_at: str) -> list[Claim]:
    """Organisation facts from schema.org JSON-LD published by the site itself."""
    claims: list[Claim] = []
    for item in page.jsonld:
        types = item.get("@type")
        types = types if isinstance(types, list) else [types]
        if not any(t in {"Organization", "Corporation", "LocalBusiness", "ProfessionalService", "Store",
                         "EmploymentAgency", "EducationalOrganization"} or (isinstance(t, str) and t.endswith("Business"))
                   for t in types):
            continue
        raw = str({k: item.get(k) for k in ("name", "legalName", "numberOfEmployees", "foundingDate", "address")})[:400]

        def add(ctype: str, value: str) -> None:
            claims.append(Claim(claim_type=ctype, value=value[:300], evidence=_ev(page, raw, retrieved_at),
                                extractor="rule:jsonld", confidence=0.85))

        for key in ("legalName", "name"):
            if isinstance(item.get(key), str):
                add("structured_name", item[key])
        add("structured_type", ",".join(str(t) for t in types if t))
        emp = item.get("numberOfEmployees")
        if isinstance(emp, dict):
            emp = emp.get("value") or emp.get("maxValue") or emp.get("minValue")
        if emp is not None:
            try:
                n = int(str(emp).replace(",", "").replace("+", ""))
                add("size_statement", f"numberOfEmployees {emp}|{n}")
            except ValueError:
                pass
        if isinstance(item.get("foundingDate"), str):
            add("founding_date", item["foundingDate"][:10])
        addr = item.get("address")
        if isinstance(addr, dict):
            parts = [addr.get(k) for k in ("streetAddress", "addressLocality", "addressRegion", "addressCountry")]
            joined = ", ".join(str(p) for p in parts if isinstance(p, str))
            if joined:
                add("structured_address", joined)
        elif isinstance(addr, str):
            add("structured_address", addr)
        if types == ["EmploymentAgency"]:
            add("business_type_signal", "recruitment_agency|schema.org EmploymentAgency")
        if "EducationalOrganization" in types:
            add("business_type_signal", "training_provider|schema.org EducationalOrganization")
    return claims
