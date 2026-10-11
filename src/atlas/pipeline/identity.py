"""Website identity verification and (optional) website resolution.

Identity: does the website actually belong to the company we think it does?
We compare the company name with the names the site gives itself (title,
og:site_name, JSON-LD name, copyright holder) and with the domain label.

Website resolution: for candidates with no website (e.g. OpenStreetMap entries),
try a handful of domains derived from the company name. A domain is accepted
only if (1) it resolves, (2) robots.txt allows fetching the homepage, (3) the
homepage names the company (similarity ≥ 85) and (4) the page shows UAE
presence. Accepted websites are labelled with source ``website_resolution`` and
lower confidence. This never produces email addresses.
"""

from __future__ import annotations

import re

import dns.asyncresolver
import dns.exception
from rapidfuzz import fuzz

from atlas.assess.taxonomy import GENERIC_NAME_TOKENS
from atlas.crawl.html import parse_html
from atlas.extract.signals import UAE_SIGNAL
from atlas.net.fetcher import Fetcher
from atlas.resolve.normalize import normalize_name, registrable_domain

TITLE_SPLIT = re.compile(r"\s+[|–—:•·»-]\s+|\s+-\s+")
COPYRIGHT_HOLDER = re.compile(r"(?:©|copyright)\s*(?:\d{4}\s*[-–]?\s*\d{0,4})?\s*([A-Z][\w&.,' -]{2,60}?)(?:\.|\||all rights| -|$)",
                              re.IGNORECASE)


def site_names(title: str, og_site_name: str = "", structured: list[str] | None = None, footer: str = "") -> list[str]:
    names = []
    if og_site_name:
        names.append(og_site_name)
    names += structured or []
    if title:
        names += [p.strip() for p in TITLE_SPLIT.split(title) if 2 <= len(p.strip()) <= 80]
    m = COPYRIGHT_HOLDER.search(footer or "")
    if m:
        names.append(m.group(1).strip(" ,."))
    seen, out = set(), []
    for n in names:
        k = n.lower()
        if k not in seen:
            seen.add(k)
            out.append(n)
    return out


def name_match_score(company_name: str, candidates: list[str], domain: str | None) -> tuple[float, str]:
    norm = normalize_name(company_name)
    best, why = 0.0, ""
    for c in candidates:
        cn = normalize_name(c)
        if not cn:
            continue
        s = max(fuzz.token_set_ratio(norm, cn), fuzz.ratio(norm, cn))
        if s > best:
            best, why = s, f"site name “{c}”"
    if domain:
        label = domain.split(".")[0].replace("-", "")
        squashed = norm.replace(" ", "")
        distinctive = "".join(t for t in norm.split() if t not in GENERIC_NAME_TOKENS)
        s = max(fuzz.ratio(label, squashed), fuzz.ratio(label, distinctive) if distinctive else 0)
        if s > best:
            best, why = s, f"domain {domain}"
    return best, why


def identity_status(company_name: str, names: list[str], domain: str | None) -> tuple[str, str]:
    score, why = name_match_score(company_name, names, domain)
    if score >= 80:
        return "verified", f"company name matches {why} ({score:.0f})"
    if names and score < 50:
        return "mismatch", f"site presents itself as “{names[0]}”, which does not match the company name ({score:.0f})"
    return "unverified", f"weak name match ({score:.0f}) — check manually"


def candidate_domains(name: str, max_candidates: int = 4) -> list[str]:
    tokens = [t for t in normalize_name(name).split() if t.isascii() and t.isalnum()]
    distinctive = [t for t in tokens if t not in GENERIC_NAME_TOKENS]
    if not distinctive or sum(len(t) for t in distinctive) < 4:
        return []  # too generic to guess safely
    bases = []
    for toks in (distinctive, tokens):
        if 1 <= len(toks) <= 4:
            bases += ["".join(toks), "-".join(toks)]
    out: list[str] = []
    for b in dict.fromkeys(bases):
        for tld in ("ae", "com"):
            d = f"{b}.{tld}"
            if d not in out and len(b) >= 4:
                out.append(d)
    return out[:max_candidates]


async def _resolves(domain: str, timeout: float = 4.0) -> bool:
    r = dns.asyncresolver.Resolver()
    r.lifetime = timeout
    try:
        await r.resolve(domain, "A")
        return True
    except dns.exception.DNSException:
        try:
            await r.resolve("www." + domain, "A")
            return True
        except dns.exception.DNSException:
            return False


async def resolve_website(fetcher: Fetcher, company_name: str) -> tuple[str | None, str]:
    """Return (website root, evidence note) or (None, reason)."""
    tried = []
    for dom in candidate_domains(company_name):
        if not await _resolves(dom):
            tried.append(f"{dom}: no DNS")
            continue
        url = f"https://{dom}/"
        res = await fetcher.get(url, retries=0)
        if res.robots_blocked:
            tried.append(f"{dom}: robots.txt disallows")
            continue
        if not res.ok:
            tried.append(f"{dom}: {res.error or res.status}")
            continue
        page = parse_html(res.text(), res.final_url)
        if page.is_placeholder or page.is_bot_challenge:
            tried.append(f"{dom}: placeholder/blocked page")
            continue
        structured = [i.get("name") for i in page.jsonld if isinstance(i.get("name"), str)]
        names = site_names(page.title, page.og_site_name, structured, page.footer_text)
        score, why = name_match_score(company_name, names, None)  # page must name the company, not just the domain
        uae = UAE_SIGNAL.search(page.full_text) or re.search(r"\b(dubai|abu dhabi|sharjah|ajman)\b", page.full_text, re.I)
        if score >= 85 and uae:
            final_dom = registrable_domain(res.final_url) or dom
            return f"https://{final_dom}/" if final_dom != dom else url, (
                f"name-derived domain {dom} verified: {why} ({score:.0f}) and page mentions “{uae.group(0)}”")
        tried.append(f"{dom}: name match {score:.0f}{'' if uae else ', no UAE signal'}")
    return None, "; ".join(tried) or "name too generic to derive a domain"
