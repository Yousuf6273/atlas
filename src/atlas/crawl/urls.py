"""URL canonicalisation and page-type classification for focused crawling."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from atlas.resolve.normalize import registrable_domain

TRACKING_PARAMS = re.compile(r"^(utm_\w+|gclid|fbclid|msclkid|mc_cid|mc_eid|_ga|_gl|ref|refsrc|hsCtaTracking|"
                             r"__hstc|__hssc|__hsfp|hsLang|trk|igshid|yclid|srsltid)$", re.IGNORECASE)

SKIP_EXTENSIONS = re.compile(
    r"\.(pdf|jpe?g|png|gif|svg|webp|bmp|ico|tiff?|zip|rar|7z|gz|tar|docx?|xlsx?|pptx?|odt|mp4|mp3|m4a|avi|mov|wmv|"
    r"webm|css|js|json|xml|rss|atom|woff2?|ttf|eot|exe|dmg|apk|ics|vcf|csv)$", re.IGNORECASE)

SKIP_PATHS = re.compile(
    r"(/wp-admin|/wp-login|/wp-json|/xmlrpc|/login|/log-in|/signin|/sign-in|/signup|/sign-up|/register|/cart|"
    r"/checkout|/basket|/account|/my-account|/feed/?$|/tag/|/tags/|/category/|/categories/|/author/|/search|"
    r"/privacy|/cookie|/terms|/legal|/disclaimer|/sitemap|/cdn-cgi/|/page/\d+|/comments|/share|/print/|"
    r"/attachment/|/wishlist|/compare|/add-to-cart|/calendar|/events?/\d{4})", re.IGNORECASE)

LANG_PREFIX = re.compile(r"^/(ar|ar-ae|ae-ar|fr|de|es|ru|zh|zh-cn|cn|it|pt|tr|ja|ko|hi|ur|fa|nl|pl)(/|$)", re.IGNORECASE)

# page type → (url path regex, anchor text regex, priority)
PAGE_TYPES: list[tuple[str, re.Pattern[str], re.Pattern[str], int]] = [
    ("internship",
     re.compile(r"intern(ship)?s?|graduate|student|work-?experience|early-?careers?|summer-?program", re.I),
     re.compile(r"\b(internships?|interns?|graduate (program|programme|scheme)s?|students?|work experience|"
                r"early careers?|summer (program|programme|placement)s?)\b", re.I), 95),
    ("about", re.compile(r"/(about|about-?us|who-?we-?are|company|our-?story|overview|profile|corporate|history|"
                         r"mission|vision|introduction|at-a-glance)(/|$|\.)", re.I),
     re.compile(r"\b(about( us)?|who we are|our (story|company|history)|company profile|overview)\b", re.I), 90),
    ("contact", re.compile(r"/(contact|contact-?us|get-?in-?touch|reach-?us|enquir(y|ies)|inquir(y|ies)|"
                           r"locations?|offices?|find-?us)(/|$|\.)", re.I),
     re.compile(r"\b(contact( us)?|get in touch|enquir(y|ies)|reach us|our offices|locations)\b", re.I), 88),
    ("careers", re.compile(r"/(careers?|jobs?|join-?us|join-?our-?team|work-?with-?us|vacanc(y|ies)|"
                           r"opportunities|recruitment|hiring|life-?at)(/|$|\.)", re.I),
     re.compile(r"\b(careers?|jobs|join (us|our team)|work with us|vacanc(y|ies)|we'?re hiring|opportunities)\b", re.I), 85),
    ("services", re.compile(r"/(services?|solutions?|what-?we-?do|capabilit(y|ies)|expertise|offerings?|"
                            r"engineering)(/|$|\.)", re.I),
     re.compile(r"\b(services|solutions|what we do|capabilities|expertise|our work)\b", re.I), 80),
    ("team", re.compile(r"/(team|our-?team|people|leadership|management|board|founders?|executives?|"
                        r"meet-?the-?team|staff)(/|$|\.)", re.I),
     re.compile(r"\b(team|our people|leadership|management|board|founders?|meet the team)\b", re.I), 75),
    ("products", re.compile(r"/(products?|catalogu?e?|range|technology|technologies|platform|systems)(/|$|\.)", re.I),
     re.compile(r"\b(products?|catalogu?e|technology|platform|our systems)\b", re.I), 70),
    ("projects", re.compile(r"/(projects?|case-?studies|portfolio|references|clients|work|success-?stories)(/|$|\.)", re.I),
     re.compile(r"\b(projects|case studies|portfolio|references|clients|our work)\b", re.I), 65),
    ("industries", re.compile(r"/(industries|sectors|markets|applications)(/|$|\.)", re.I),
     re.compile(r"\b(industries|sectors|markets( we serve)?|applications)\b", re.I), 60),
    ("news", re.compile(r"/(news|blog|press|media|media-cent(re|er)|insights|updates|articles|newsroom|announcements|"
                        r"stories)(/|$|\.)", re.I),
     re.compile(r"\b(news|blog|press|media|insights|updates|newsroom)\b", re.I), 40),
]

PRIORITY = {name: prio for name, _u, _a, prio in PAGE_TYPES} | {"home": 100, "other": 10}

# Maximum number of pages per type in one crawl.
TYPE_CAPS = {"home": 1, "about": 2, "contact": 2, "careers": 2, "internship": 2, "services": 3, "team": 2,
             "products": 2, "projects": 2, "industries": 1, "news": 2, "other": 1}


def canonicalize(url: str) -> str:
    p = urlsplit(url.strip())
    scheme = (p.scheme or "https").lower()
    host = (p.hostname or "").lower()
    port = f":{p.port}" if p.port and not ((scheme == "http" and p.port == 80) or (scheme == "https" and p.port == 443)) else ""
    path = re.sub(r"/{2,}", "/", p.path or "/")
    path = re.sub(r"/(index|default)\.(html?|php|aspx?)$", "/", path, flags=re.I)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)))
    return urlunsplit((scheme, host + port, path or "/", query, ""))


def absolutize(base: str, href: str) -> str | None:
    href = (href or "").strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:", "whatsapp:", "sms:", "skype:")):
        return None
    full = urljoin(base, href)
    if not full.lower().startswith(("http://", "https://")):
        return None
    return full


def same_site(url: str, site_domain: str) -> bool:
    return registrable_domain(url) == site_domain


def should_skip(url: str) -> bool:
    p = urlsplit(url)
    path = p.path or "/"
    return bool(SKIP_EXTENSIONS.search(path) or SKIP_PATHS.search(path) or LANG_PREFIX.match(path))


NEWS_SECTION = re.compile(r"^/(news|blog|blogs|press|media|media-cent(re|er)|newsroom|insights|articles|stories|"
                          r"updates|announcements|events|press-releases?)/.+", re.IGNORECASE)


def classify(url: str, anchor_text: str = "") -> str:
    path = urlsplit(url).path or "/"
    if path in ("", "/"):
        return "home"
    if NEWS_SECTION.match(path):
        return "news"  # an article about students is still an article, not an internship page
    best, best_prio = "other", 0
    for name, url_re, anchor_re, prio in PAGE_TYPES:
        if (url_re.search(path) or (anchor_text and anchor_re.search(anchor_text))) and prio > best_prio:
            best, best_prio = name, prio
    return best


WELL_KNOWN_FALLBACKS = ("/about", "/about-us", "/contact", "/contact-us", "/careers")
