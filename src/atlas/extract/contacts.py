"""Contact-channel extraction from a company's own web pages.

Only information the company itself publishes is extracted. No addresses are
guessed or constructed from name patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import phonenumbers

from atlas.crawl.html import ParsedPage
from atlas.models.contact import ContactCandidate
from atlas.models.enums import ContactChannel, RoleCategory
from atlas.resolve.normalize import registrable_domain

EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+\-]{1,64})@([A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63})*\.[A-Za-z]{2,24})(?![\w-])")
OBFUSCATED_RE = re.compile(
    r"(?<![\w.])([A-Za-z0-9._%+\-]{1,64})\s*[\[\(\{]\s*(?:at|@)\s*[\]\)\}]\s*([A-Za-z0-9\-]{1,63}(?:\s*[\[\(\{]\s*(?:dot|\.)\s*[\]\)\}]\s*[A-Za-z0-9\-]{1,63})+)",
    re.IGNORECASE)
FILE_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "ico", "pdf", "mp4", "woff", "woff2", "ttf"}
PLACEHOLDER_DOMAINS = {"example.com", "domain.com", "email.com", "yourdomain.com", "company.com", "website.com",
                       "sentry.io", "wixpress.com", "sentry-next.wixpress.com", "mysite.com", "test.com"}
PLACEHOLDER_LOCALS = {"yourname", "your.name", "name", "user", "username", "email", "youremail", "your-email",
                      "john.doe", "jane.doe", "johndoe", "someone", "firstname.lastname", "first.last", "xxx"}
FREE_MAIL = {"gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com", "yahoo.com", "icloud.com",
             "me.com", "aol.com", "proton.me", "protonmail.com", "yandex.com", "mail.com", "gmx.com", "msn.com"}

ROLE_LOCALS: list[tuple[re.Pattern[str], RoleCategory]] = [
    (re.compile(r"^(careers?|jobs?|hr|recruit(ment|ing)?|talent|hiring|people|internships?|interns?|vacanc(y|ies)|cv|resumes?|join)$"), RoleCategory.HR_TALENT),
    (re.compile(r"^(engineering|engineers?|tech(nical)?|rnd|r&d|research|innovation|design)$"), RoleCategory.ENGINEERING_LEAD),
    (re.compile(r"^(operations?|ops|projects?|production|plant|quality|qa|qc)$"), RoleCategory.OPERATIONS),
    (re.compile(r"^(ceo|md|founders?|director|chairman|gm|managing\.?director|office\.?of\.?the\.?ceo)$"), RoleCategory.FOUNDER_EXEC),
    (re.compile(r"^(sales|marketing|business|bd|biz|quotes?|quotations?|orders?|procurement|purchas(e|ing)|rfq|tenders?|export)$"), RoleCategory.SALES),
    (re.compile(r"^(accounts?|finance|billing|invoices?|payments?|ar|ap|support|service|services|help|helpdesk|customercare|customer\.?service|cs|care|complaints?|media|press|pr|news|webmaster|privacy|legal|compliance|it|noreply|no-reply|donotreply)$"), RoleCategory.OTHER),
    (re.compile(r"^(info|information|contact|contactus|contact-us|hello|hi|hey|enquir(y|ies|e)|inquir(y|ies|e)|office|admin|mail|general|team|reception|uae|dubai|ae|me|connect|ask|assist|reach|talk|letstalk|start|partners?|partnerships?|collab|hq|studio|labs?|contacto)$"), RoleCategory.GENERAL),
]

ROLE_TITLES: list[tuple[re.Pattern[str], RoleCategory]] = [
    (re.compile(r"\b(chief executive officer|ceo|co-?founder|founder|managing director|general manager|chairman|"
                r"managing partner|owner|president)\b", re.I), RoleCategory.FOUNDER_EXEC),
    (re.compile(r"\b(chief technology officer|cto|technical director|engineering (manager|director|lead)|"
                r"head of engineering|director of engineering|vp,? engineering|chief engineer|lead engineer|"
                r"technical manager)\b", re.I), RoleCategory.ENGINEERING_LEAD),
    (re.compile(r"\b(r&d (manager|director|lead)|head of r&d|research (director|manager|lead)|head of research|"
                r"chief scientist|head of innovation)\b", re.I), RoleCategory.RND),
    (re.compile(r"\b(chief operating officer|coo|operations (manager|director)|head of operations|plant manager|"
                r"production manager|factory manager)\b", re.I), RoleCategory.OPERATIONS),
    (re.compile(r"\b(project (manager|director)|head of projects|programme manager|program manager)\b", re.I), RoleCategory.PROJECT),
    (re.compile(r"\b(hr (manager|director|business partner|lead)|human resources|head of (hr|people)|"
                r"talent acquisition|recruit(er|ment) (manager|lead)|people (manager|operations|partner))\b", re.I), RoleCategory.HR_TALENT),
    (re.compile(r"\b(sales (manager|director)|business development|head of sales|commercial director|"
                r"marketing (manager|director))\b", re.I), RoleCategory.SALES),
]

NAME_TOKEN = r"(?:[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?|[A-Z]\.|Al|Bin|bin|El|Abu|de|van|von)"
NAME_RE = re.compile(rf"^(?:(?:Mr|Mrs|Ms|Dr|Eng|Engr|Prof)\.?\s+)?({NAME_TOKEN}(?:\s+{NAME_TOKEN}){{1,3}})$")
NOT_NAME_WORDS = {
    "our", "the", "team", "company", "services", "service", "dubai", "uae", "llc", "engineering", "manager",
    "director", "officer", "chief", "head", "about", "contact", "us", "home", "news", "careers", "read", "more",
    "view", "profile", "meet", "board", "management", "leadership", "executive", "group", "solutions", "technologies",
    "abu", "dhabi", "sharjah", "middle", "east", "global", "international", "learn", "founder", "ceo", "partner",
    "client", "clients", "project", "projects", "innovation", "quality", "safety", "mission", "vision", "values",
}

ATS_HOSTS = ("lever.co", "greenhouse.io", "workable.com", "bamboohr.com", "smartrecruiters.com", "recruitee.com",
             "breezy.hr", "zohorecruit", "myworkdayjobs.com", "successfactors", "taleo.net", "bayt.com",
             "teamtailor.com", "personio", "jobvite.com", "icims.com")


@dataclass
class PersonRole:
    name: str
    role: str
    role_category: RoleCategory
    snippet: str
    confidence: float


def classify_local_part(local: str) -> tuple[RoleCategory, bool]:
    """Return (role category, is_generic) for an email local part."""
    l = local.lower()
    for pat, cat in ROLE_LOCALS:
        if pat.match(l):
            return cat, True
    # Role words embedded in longer locals, e.g. "careers.uae", "hr-dubai".
    for part in re.split(r"[._\-+]", l):
        for pat, cat in ROLE_LOCALS:
            if pat.match(part):
                return cat, True
    return RoleCategory.OTHER, False  # looks like an individual's mailbox


def _valid_email(local: str, domain: str) -> bool:
    d = domain.lower()
    if d.rsplit(".", 1)[-1] in FILE_TLDS:
        return False
    if d in PLACEHOLDER_DOMAINS or local.lower() in PLACEHOLDER_LOCALS:
        return False
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    return True


def find_emails(text: str) -> list[tuple[str, str]]:
    """(email, snippet) pairs found in plain text, including simple obfuscations."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in EMAIL_RE.finditer(text):
        local, dom = m.group(1), m.group(2)
        email = f"{local}@{dom}".lower()
        if email in seen or not _valid_email(local, dom):
            continue
        seen.add(email)
        out.append((email, _window(text, m.start(), m.end())))
    for m in OBFUSCATED_RE.finditer(text):
        dom = re.sub(r"\s*[\[\(\{]\s*(?:dot|\.)\s*[\]\)\}]\s*", ".", m.group(2), flags=re.I)
        email = f"{m.group(1)}@{dom}".lower()
        if email in seen or not _valid_email(m.group(1), dom):
            continue
        seen.add(email)
        out.append((email, _window(text, m.start(), m.end())))
    return out


def _window(text: str, start: int, end: int, width: int = 120) -> str:
    s = max(0, start - width)
    e = min(len(text), end + width)
    return " ".join(text[s:e].split())


def role_from_text(text: str) -> tuple[str, RoleCategory] | None:
    for pat, cat in ROLE_TITLES:
        m = pat.search(text)
        if m:
            return m.group(0), cat
    return None


def _looks_like_name(s: str) -> bool:
    s = s.strip(" ,–-:|")
    m = NAME_RE.match(s)
    if not m:
        return False
    words = [w.lower().strip(".") for w in m.group(1).split()]
    return not any(w in NOT_NAME_WORDS for w in words) and sum(len(w) > 1 for w in words) >= 2


def extract_people(page: ParsedPage) -> list[PersonRole]:
    """Named people published next to a role title (team/leadership/about pages)."""
    people: list[PersonRole] = []
    seen: set[str] = set()

    def add(name: str, role: str, cat: RoleCategory, snippet: str, conf: float) -> None:
        name = re.sub(r"^(Mr|Mrs|Ms|Dr|Eng|Engr|Prof)\.?\s+", "", name.strip(" ,–-:|"))
        key = name.lower()
        if key not in seen:
            seen.add(key)
            people.append(PersonRole(name, role.strip(), cat, snippet[:300], conf))

    for item in page.jsonld:
        types = item.get("@type")
        types = types if isinstance(types, list) else [types]
        if "Person" in types and isinstance(item.get("name"), str) and isinstance(item.get("jobTitle"), str):
            rr = role_from_text(item["jobTitle"])
            if rr and _looks_like_name(item["name"]):
                add(item["name"], item["jobTitle"], rr[1], f"{item['name']} — {item['jobTitle']}", 0.85)

    blocks = page.blocks
    for i, b in enumerate(blocks):
        if len(b) > 90:
            continue
        rr = role_from_text(b)
        if not rr:
            continue
        # Same block: "Jane Roe – Engineering Manager" / "CEO: Jane Roe"
        for part in re.split(r"\s*[,–\-|:]\s*|\s+—\s+", b):
            if part and _looks_like_name(part):
                add(part, rr[0], rr[1], b, 0.65)
                break
        else:
            # Adjacent blocks: name immediately before (most common) or after the title.
            for j in (i - 1, i + 1):
                if 0 <= j < len(blocks) and len(blocks[j]) <= 40 and _looks_like_name(blocks[j]):
                    add(blocks[j], rr[0], rr[1], f"{blocks[j]} — {b}", 0.6)
                    break
    return people


def extract_contacts(page: ParsedPage, page_type: str, site_domain: str | None, retrieved_at: str) -> list[ContactCandidate]:
    url = page.url
    out: list[ContactCandidate] = []
    emails: dict[str, str] = {}
    for addr, ctx in page.mailtos:
        m = EMAIL_RE.search(addr)
        if m and _valid_email(m.group(1), m.group(2)):
            emails.setdefault(f"{m.group(1)}@{m.group(2)}".lower(), ctx)
    for addr, snippet in find_emails(page.full_text):
        emails.setdefault(addr, snippet)
    for item in page.jsonld:
        e = item.get("email")
        if isinstance(e, str):
            m = EMAIL_RE.search(e.replace("mailto:", ""))
            if m and _valid_email(m.group(1), m.group(2)):
                emails.setdefault(f"{m.group(1)}@{m.group(2)}".lower(), f"structured data: {e}")

    people = extract_people(page) if page_type in ("team", "about", "contact", "home", "careers") else []
    for email, snippet in emails.items():
        local = email.split("@", 1)[0]
        cat, generic = classify_local_part(local)
        name = role = None
        if not generic:
            # Attach a published name/role only if they appear in the same snippet.
            for p in people:
                if p.name.lower().split()[0] in snippet.lower() or p.name.lower().split()[-1] in local:
                    name, role, cat = p.name, p.role, p.role_category
                    break
            else:
                rr = role_from_text(snippet)
                if rr:
                    role, cat = rr
        out.append(ContactCandidate(
            channel=ContactChannel.EMAIL, value=email, name=name, role=role, role_category=cat, is_generic=generic,
            source_type="official_site", source_url=url, snippet=snippet, retrieved_at=retrieved_at))

    for p in people:
        # A named person without a published email is still useful (role to address, LinkedIn search by user).
        out.append(ContactCandidate(
            channel=ContactChannel.NAMED_PERSON, value=f"{p.name} | {url}", name=p.name, role=p.role,
            role_category=p.role_category, is_generic=False, source_type="official_site", source_url=url,
            snippet=p.snippet, retrieved_at=retrieved_at))

    phones: dict[str, str] = {}
    for t in page.tels:
        try:
            n = phonenumbers.parse(t, "AE")
            if phonenumbers.is_possible_number(n):
                phones.setdefault(phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164), f"tel: {t}")
        except phonenumbers.NumberParseException:
            continue
    for match in phonenumbers.PhoneNumberMatcher(page.full_text[:20000], "AE"):
        e164 = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
        phones.setdefault(e164, _window(page.full_text, match.start, match.end, 60))
        if len(phones) >= 6:
            break
    for e164, snippet in phones.items():
        out.append(ContactCandidate(channel=ContactChannel.PHONE, value=e164, role_category=RoleCategory.GENERAL,
                                    source_type="official_site", source_url=url, snippet=snippet,
                                    retrieved_at=retrieved_at))

    for f in page.forms:
        names = " ".join(f.field_names)
        if f.has_email_field and f.has_message_field and "search" not in names and "password" not in names:
            out.append(ContactCandidate(channel=ContactChannel.CONTACT_FORM, value=url, role_category=RoleCategory.GENERAL,
                                        source_type="official_site", source_url=url,
                                        snippet=f"Contact form with fields: {', '.join(f.field_names[:8])}",
                                        retrieved_at=retrieved_at))
            break

    if page_type in ("careers", "internship"):
        out.append(ContactCandidate(channel=ContactChannel.CAREERS_PAGE, value=url, role_category=RoleCategory.HR_TALENT,
                                    source_type="official_site", source_url=url,
                                    snippet=(page.title or "careers page")[:200], retrieved_at=retrieved_at))
    for link in page.links:
        low = link.url.lower()
        if "linkedin.com/company/" in low:
            clean = link.url.split("?")[0].rstrip("/")
            out.append(ContactCandidate(channel=ContactChannel.LINKEDIN_COMPANY, value=clean,
                                        role_category=RoleCategory.GENERAL, source_type="official_site",
                                        source_url=url, snippet=f"LinkedIn link on {url}", retrieved_at=retrieved_at))
        elif any(h in low for h in ATS_HOSTS) and registrable_domain(link.url) != site_domain:
            out.append(ContactCandidate(channel=ContactChannel.CAREERS_PAGE, value=link.url.split("#")[0],
                                        role_category=RoleCategory.HR_TALENT, source_type="official_site",
                                        source_url=url, snippet=f"Jobs portal linked from {url}: {link.text[:80]}",
                                        retrieved_at=retrieved_at))
    return _dedupe(out)


def _dedupe(items: list[ContactCandidate]) -> list[ContactCandidate]:
    seen: set[tuple[str, str]] = set()
    out = []
    for c in items:
        key = (c.channel.value, c.value.lower().rstrip("/"))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def email_domain_relation(email: str, site_domain: str | None) -> str:
    dom = registrable_domain(email.split("@", 1)[1]) if "@" in email else None
    if dom and site_domain and dom == site_domain:
        return "company_domain"
    if dom in FREE_MAIL or (email.split("@", 1)[1].lower() in FREE_MAIL):
        return "free_mail_provider"
    return "other_domain"
