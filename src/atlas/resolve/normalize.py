"""Normalisation of names, domains, phone numbers, addresses and URLs."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from urllib.parse import urlsplit

import phonenumbers
import tldextract

# Legal-form suffixes and qualifiers removed when comparing names. Order
# matters: multi-token forms first.
_LEGAL_FORMS = [
    r"l\.?\s?l\.?\s?c\.?", r"f\.?\s?z\.?\s?-?\s?l\.?\s?l\.?\s?c\.?", r"f\.?\s?z\.?\s?c\.?\s?o\.?", r"f\.?\s?z\.?\s?e\.?",
    r"f\.?\s?z\.?\s?c\.?", r"fz", r"dmcc", r"dwc", r"jlt", r"ltd\.?", r"limited", r"inc\.?", r"incorporated",
    r"corp\.?", r"corporation", r"co\.?", r"company", r"est\.?", r"establishment", r"p\.?j\.?s\.?c\.?",
    r"p\.?s\.?c\.?", r"plc", r"w\.?l\.?l\.?", r"s\.?p\.?c\.?", r"llp", r"gmbh", r"ag", r"s\.?a\.?", r"b\.?v\.?",
    r"pvt\.?", r"private", r"sole proprietorship(?: llc)?", r"one person company", r"opc", r"branch",
]
_LEGAL_RE = re.compile(r"(?<![a-z0-9])(?:" + "|".join(_LEGAL_FORMS) + r")(?![a-z0-9])", re.IGNORECASE)
_BRANCH_RE = re.compile(r"\(?\s*branch(?: of [^)]*)?\)?|\bbranch\b", re.IGNORECASE)
_PAREN_RE = re.compile(r"\([^)]*\)")
_NON_ALNUM = re.compile(r"[^a-z0-9& ]+")
_WS = re.compile(r"\s+")

# Hosts that are never a company's own website.
NON_COMPANY_HOSTS = {
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "tiktok.com",
    "wa.me", "whatsapp.com", "linktr.ee", "google.com", "goo.gl", "maps.app.goo.gl", "business.site",
    "bing.com", "yelp.com", "yellowpages.ae", "dubizzle.com", "wikipedia.org", "wikidata.org", "t.me",
    "pinterest.com", "medium.com", "github.io", "blogspot.com", "wordpress.com", "wixsite.com", "about.me",
    "openstreetmap.org", "bit.ly", "tinyurl.com", "carrd.co",
}

# Bundled public-suffix snapshot, no network. RFC 2606/6761 reserved TLDs are
# added so synthetic test fixtures (*.example, *.test) resolve like real domains.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None, extra_suffixes=("example", "test", "invalid"))


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_name(name: str) -> str:
    """Lower-case name with legal forms, branch qualifiers and punctuation removed."""
    s = strip_accents(name or "").lower()
    s = s.replace("&", " and ").replace("+", " and ")
    s = _BRANCH_RE.sub(" ", s)
    s = _PAREN_RE.sub(" ", s)
    s = _LEGAL_RE.sub(" ", s)
    s = _NON_ALNUM.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s or (name or "").strip().lower()


def name_tokens(name: str) -> list[str]:
    return [t for t in normalize_name(name).split() if t not in {"and", "the", "of"}]


def has_branch_marker(name: str) -> bool:
    return bool(re.search(r"\bbranch\b", name or "", re.IGNORECASE))


def normalize_url(url: str | None) -> str | None:
    """Return an absolute http(s) URL or None. Adds a scheme if missing."""
    if not url:
        return None
    u = url.strip().strip("\"'<>")
    if not u or " " in u:
        return None
    if u.startswith("//"):
        u = "https:" + u
    if not re.match(r"^https?://", u, re.IGNORECASE):
        if re.match(r"^[a-z][a-z0-9+.-]*:", u, re.IGNORECASE) and not re.match(r"^[\w.-]+:\d", u):
            return None  # mailto:, tel:, javascript: …
        u = "https://" + u
    parts = urlsplit(u)
    if not parts.hostname or "." not in parts.hostname:
        return None
    return u


@lru_cache(maxsize=20000)
def registrable_domain(url_or_host: str | None) -> str | None:
    """'https://www.shop.acme.co.uk/x' -> 'acme.co.uk'."""
    if not url_or_host:
        return None
    host = url_or_host
    if "://" in host or "/" in host:
        n = normalize_url(host)
        if not n:
            return None
        host = urlsplit(n).hostname or ""
    host = host.lower().strip(".")
    if re.fullmatch(r"[\d.]+", host):
        return None
    ext = _EXTRACT(host)
    if not ext.domain or not ext.suffix:
        return None
    return f"{ext.domain}.{ext.suffix}"


def is_company_domain(domain: str | None) -> bool:
    if not domain:
        return False
    return domain not in NON_COMPANY_HOSTS and not any(domain.endswith("." + h) for h in NON_COMPANY_HOSTS)


def website_root(url: str | None) -> str | None:
    n = normalize_url(url)
    if not n:
        return None
    p = urlsplit(n)
    scheme = (p.scheme or "https").lower()
    host = (p.hostname or "").lower()
    port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
    return f"{scheme}://{host}{port}/"


def normalize_phone(raw: str | None, default_region: str = "AE") -> str | None:
    """E.164 string, or None when the number is not valid."""
    if not raw:
        return None
    try:
        num = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return None
    # "Possible" (correct length/shape) rather than "valid": libphonenumber's
    # allocation metadata lags new UAE number ranges.
    if not phonenumbers.is_possible_number(num):
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


_ADDR_ABBREV = {
    r"\bst\b\.?": "street", r"\brd\b\.?": "road", r"\bbldg\b\.?": "building", r"\bflr?\b\.?": "floor",
    r"\bp\.?\s?o\.?\s?box\b": "po box", r"\bu\.?a\.?e\b\.?": "uae",
}


def normalize_address(addr: str | None) -> str | None:
    if not addr:
        return None
    s = strip_accents(addr).lower()
    for pat, rep in _ADDR_ABBREV.items():
        s = re.sub(pat, rep, s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return _WS.sub(" ", s).strip() or None


def clean_text(s: str | None) -> str:
    """Remove control/zero-width characters and collapse whitespace."""
    if not s:
        return ""
    s = "".join(ch for ch in s if ch in "\n\t" or (unicodedata.category(ch)[0] != "C"))
    s = re.sub(r"[​-‏ - ⁠-⁯﻿]", "", s)
    return _WS.sub(" ", s).strip()
