"""HTML parsing and clean-up.

Web content is untrusted: it is parsed as data only. Nothing extracted here is
ever executed or interpreted as an instruction.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Comment, Tag

from atlas.crawl.urls import absolutize
from atlas.resolve.normalize import clean_text

BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "dt", "dd", "blockquote", "figcaption",
              "address", "caption", "summary", "label"}
CONTAINER_TAGS = {"div", "section", "article", "span", "main", "aside", "header", "footer", "nav", "a", "strong", "em",
                  "b", "i", "small"}
DROP_TAGS = ["script", "style", "noscript", "svg", "template", "iframe", "canvas", "object", "embed", "video", "audio",
             "picture", "source", "map"]

BOT_CHALLENGE = re.compile(
    r"(just a moment\.\.\.|attention required!? \| cloudflare|checking your browser|vercel security checkpoint|"
    r"ddos-guard|please enable cookies|verify you are human|are you a robot|captcha|access denied|"
    r"request unsuccessful\. incapsula|pardon our interruption)", re.IGNORECASE)

PLACEHOLDER = re.compile(
    r"(this domain (is|may be) for sale|buy this domain|domain (is )?parked|parked (free|domain)|"
    r"website (is )?coming soon|site (is )?under construction|under maintenance|account (has been )?suspended|"
    r"welcome to nginx|apache2 (ubuntu|debian) default page|it works!|index of /|default web site page|"
    r"hostinger|future home of something quite cool|this site can.t be reached)", re.IGNORECASE)

JS_REQUIRED = re.compile(r"(you need to enable javascript|please enable javascript|javascript is (required|disabled)|"
                         r"requires javascript)", re.IGNORECASE)


@dataclass
class Link:
    url: str
    text: str


@dataclass
class FormInfo:
    action: str | None
    method: str
    field_names: list[str]
    has_email_field: bool
    has_message_field: bool
    submit_text: str


@dataclass
class ParsedPage:
    url: str
    title: str = ""
    meta_description: str = ""
    og_site_name: str = ""
    og_title: str = ""
    canonical: str | None = None
    lang: str | None = None
    headings: list[tuple[str, str]] = field(default_factory=list)  # (tag, text)
    blocks: list[str] = field(default_factory=list)  # main-content text blocks
    footer_text: str = ""
    links: list[Link] = field(default_factory=list)
    jsonld: list[dict] = field(default_factory=list)
    forms: list[FormInfo] = field(default_factory=list)
    mailtos: list[tuple[str, str]] = field(default_factory=list)  # (address, surrounding text)
    tels: list[str] = field(default_factory=list)
    script_count: int = 0
    is_bot_challenge: bool = False
    is_placeholder: bool = False
    needs_js: bool = False

    @property
    def text(self) -> str:
        return "\n".join(self.blocks)

    @property
    def full_text(self) -> str:
        return "\n".join([self.title, self.meta_description, *self.blocks, self.footer_text])

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def content_hash(self) -> str:
        norm = re.sub(r"\s+", " ", self.text.lower()).strip()
        return hashlib.sha256(norm.encode()).hexdigest()


def _text(el: Tag) -> str:
    return clean_text(el.get_text(" ", strip=True))


def parse_html(html: str, url: str) -> ParsedPage:
    soup = BeautifulSoup(html or "", "lxml")
    page = ParsedPage(url=url)
    page.script_count = len(soup.find_all("script"))

    # JSON-LD before scripts are removed.
    for s in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = s.string or s.get_text() or ""
        try:
            data = json.loads(raw.strip())
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if isinstance(it, dict) and "@graph" in it and isinstance(it["@graph"], list):
                page.jsonld.extend(x for x in it["@graph"] if isinstance(x, dict))
            elif isinstance(it, dict):
                page.jsonld.append(it)

    head = soup.head or soup
    if soup.title and soup.title.string:
        page.title = clean_text(soup.title.string)[:300]
    for m in head.find_all("meta"):
        name = (m.get("name") or m.get("property") or "").lower()
        content = clean_text(m.get("content") or "")
        if name == "description" and not page.meta_description:
            page.meta_description = content[:600]
        elif name == "og:site_name":
            page.og_site_name = content[:200]
        elif name == "og:title":
            page.og_title = content[:300]
        elif name == "og:description" and not page.meta_description:
            page.meta_description = content[:600]
    canon = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
    if canon and canon.get("href"):
        page.canonical = absolutize(url, canon["href"])
    if soup.html and soup.html.get("lang"):
        page.lang = soup.html.get("lang")[:10].lower()

    for tag in soup(DROP_TAGS):
        tag.decompose()
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()

    # Links, mailto and tel (before nav/footer are separated).
    seen_links: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        label = _text(a)[:150]
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            parent_text = _text(a.parent)[:300] if isinstance(a.parent, Tag) else label
            page.mailtos.append((addr, parent_text))
            continue
        if href.lower().startswith("tel:"):
            page.tels.append(href[4:].strip())
            continue
        full = absolutize(url, href)
        if full and full not in seen_links:
            seen_links.add(full)
            page.links.append(Link(full, label))

    for f in soup.find_all("form"):
        inputs = f.find_all(["input", "textarea", "select"])
        names = [(i.get("name") or i.get("id") or i.get("type") or "").lower() for i in inputs]
        types = [(i.get("type") or i.name).lower() for i in inputs]
        has_email = any("email" in n for n in names) or "email" in types
        has_msg = any(i.name == "textarea" for i in inputs) or any(("message" in n or "enquiry" in n) for n in names)
        submit = f.find(["button", "input"], attrs={"type": re.compile("submit", re.I)})
        page.forms.append(FormInfo(
            action=absolutize(url, f.get("action") or "") if f.get("action") else None,
            method=(f.get("method") or "get").lower(), field_names=[n for n in names if n][:30],
            has_email_field=has_email, has_message_field=has_msg,
            submit_text=(_text(submit) or (submit.get("value") if submit else "") or "")[:60] if submit else ""))

    footers = soup.find_all("footer")
    page.footer_text = clean_text(" ".join(_text(f) for f in footers))[:3000]
    for el in soup.find_all(["nav", "footer"]):
        el.decompose()

    body = soup.body or soup
    blocks: list[str] = []
    last = None
    block_or_container = list(BLOCK_TAGS | {"div", "section", "article"})
    for el in body.find_all(True):
        is_block = el.name in BLOCK_TAGS
        is_leaf_container = el.name in ("div", "section", "article") and not el.find(block_or_container)
        if not (is_block or is_leaf_container):
            continue
        if el.find_parent(list(BLOCK_TAGS)):
            continue  # nested inside a captured block (e.g. p inside li)
        t = _text(el)
        if not t or len(t) < 2 or t == last:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            page.headings.append((el.name, t[:200]))
        blocks.append(t[:2000])
        last = t
    page.blocks = _dedupe_blocks(blocks)

    probe = f"{page.title} {page.text[:1500]}"
    page.is_bot_challenge = bool(BOT_CHALLENGE.search(page.title)) or (
        page.word_count < 80 and bool(BOT_CHALLENGE.search(probe)))
    page.is_placeholder = page.word_count < 150 and bool(PLACEHOLDER.search(probe))
    page.needs_js = (page.word_count < 40 and page.script_count >= 3) or (
        page.word_count < 120 and bool(JS_REQUIRED.search(probe)))
    return page


def _dedupe_blocks(blocks: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for b in blocks:
        key = b.lower()
        if key in seen:
            continue
        # Skip a container block that merely concatenates already-seen children.
        seen.add(key)
        out.append(b)
    return out


def sentences(text: str, min_len: int = 25, max_len: int = 400) -> list[str]:
    out = []
    for block in text.split("\n"):
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", block):
            s = s.strip()
            if min_len <= len(s) <= max_len:
                out.append(s)
            elif len(s) > max_len:
                out.append(s[:max_len].rsplit(" ", 1)[0] + "…")
    return out
