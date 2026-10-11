"""Focused, budgeted crawler for a single company website.

Visits the homepage, then the most informative same-site pages (about,
services, careers/internships, team, contact …) up to a page budget. Honours
robots.txt and per-host delays through :class:`Fetcher`, skips binary and
irrelevant paths, de-duplicates identical pages and detects bot-challenge and
placeholder pages (which are recorded, never bypassed).
"""

from __future__ import annotations

import heapq
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from atlas.config import CrawlSettings
from atlas.crawl.html import ParsedPage, parse_html
from atlas.crawl.render import PlaywrightRenderer
from atlas.crawl.urls import (
    PRIORITY,
    TYPE_CAPS,
    WELL_KNOWN_FALLBACKS,
    canonicalize,
    classify,
    same_site,
    should_skip,
)
from atlas.logging_setup import get_logger, log_event
from atlas.net.fetcher import Fetcher, FetchResult
from atlas.resolve.normalize import is_company_domain, registrable_domain, website_root
from atlas.timeutil import now_iso

log = get_logger("crawl.site")

SITEMAP_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


@dataclass
class CrawledPage:
    url: str
    final_url: str
    status: int | None
    page_type: str
    fetched_at: str
    parsed: ParsedPage | None = None
    error: str | None = None
    from_cache: bool = False
    is_duplicate: bool = False
    content_type: str | None = None


@dataclass
class SiteCrawlResult:
    start_url: str
    site_domain: str | None
    pages: list[CrawledPage] = field(default_factory=list)
    reachable: bool = False
    robots_blocked: bool = False
    robots_unavailable: bool = False
    bot_challenge: bool = False
    placeholder: bool = False
    needs_js: bool = False
    redirected_to_other_domain: str | None = None
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def good_pages(self) -> list[CrawledPage]:
        return [p for p in self.pages if p.parsed is not None and not p.is_duplicate and p.error is None]


class SiteCrawler:
    def __init__(self, fetcher: Fetcher, settings: CrawlSettings, renderer: PlaywrightRenderer | None = None):
        self.fetcher = fetcher
        self.settings = settings
        self.renderer = renderer

    async def _fetch(self, url: str) -> FetchResult:
        return await self.fetcher.get(url, cache_ttl_s=self.settings.page_refresh_days * 86400)

    async def crawl(self, start_url: str, *, budget: int | None = None,
                    is_cancelled=lambda: False) -> SiteCrawlResult:
        budget = budget or self.settings.max_pages_per_site
        root = website_root(start_url)
        result = SiteCrawlResult(start_url=start_url, site_domain=registrable_domain(start_url))
        if not root or not result.site_domain:
            result.errors.append("invalid website URL")
            return result

        home = await self._fetch(root)
        if home.robots_blocked:
            policy = await self.fetcher.robots_policy(root)
            if policy.state == "unreachable" and not policy.detail.startswith("HTTP"):
                # DNS failure, TLS error or timeout: the site itself is unreachable.
                result.errors.append(f"site unreachable: {policy.detail[:160]}")
                return result
            if policy.state == "unreachable":
                result.robots_unavailable = True
                result.notes.append(f"robots.txt could not be retrieved ({policy.detail}); per RFC 9309 this is treated "
                                    "as 'disallow all', so no pages were fetched — try again later")
                return result
            result.robots_blocked = True
            result.notes.append("robots.txt disallows crawling this site; no pages were fetched")
            return result
        if not home.ok:
            # Some sites only answer on the exact URL given (deep link) or on http.
            alt = start_url if canonicalize(start_url) != canonicalize(root) else None
            if alt:
                home = await self._fetch(alt)
            if not home.ok:
                result.errors.append(f"homepage unreachable: {home.error or f'HTTP {home.status}'}")
                result.pages.append(CrawledPage(root, home.final_url, home.status, "home", now_iso(),
                                                error=home.error or f"HTTP {home.status}"))
                return result
        result.reachable = True
        final_dom = registrable_domain(home.final_url)
        if final_dom and final_dom != result.site_domain:
            if not is_company_domain(final_dom):
                result.errors.append(f"website redirects to {final_dom} (not a company website)")
                return result
            result.redirected_to_other_domain = final_dom
            result.notes.append(f"website redirects to {final_dom}; crawling that domain")
            result.site_domain = final_dom

        delay = self.fetcher.crawl_delay(home.final_url)
        if delay > 10:
            budget = min(budget, 4)
            result.notes.append(f"robots.txt crawl-delay {delay:.0f}s: page budget reduced to {budget}")

        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        type_counts: dict[str, int] = {}
        queue: list[tuple[int, int, str, str, int]] = []  # (-priority, order, url, page_type, depth)
        order = 0

        def enqueue(url: str, anchor: str, depth: int) -> None:
            nonlocal order
            if not same_site(url, result.site_domain) or should_skip(url):
                return
            c = canonicalize(url)
            if c in seen_urls:
                return
            ptype = classify(url, anchor)
            if ptype == "other" and depth > 1:
                return
            seen_urls.add(c)
            order += 1
            heapq.heappush(queue, (-PRIORITY.get(ptype, 10), order, url, ptype, depth))

        def record(res: FetchResult, url: str, ptype: str) -> CrawledPage:
            page = CrawledPage(url=url, final_url=res.final_url, status=res.status, page_type=ptype,
                               fetched_at=now_iso(), from_cache=res.from_cache, content_type=res.content_type,
                               error=res.error if not res.ok else None)
            if res.ok and res.content:
                parsed = parse_html(res.text(), res.final_url)
                page.parsed = parsed
                h = parsed.content_hash
                if h in seen_hashes:
                    page.is_duplicate = True
                seen_hashes.add(h)
            result.pages.append(page)
            return page

        seen_urls.add(canonicalize(root))
        seen_urls.add(canonicalize(home.final_url))
        home_page = record(home, root, "home")
        hp = home_page.parsed
        if hp is not None and hp.is_bot_challenge:
            result.bot_challenge = True
            home_page.error = "bot-protection challenge page (not bypassed)"
            home_page.parsed = None
            result.notes.append("site serves a bot-protection challenge to automated clients; ATLAS does not bypass "
                                "it — research this company manually")
            return result
        if hp is not None and hp.is_placeholder:
            result.placeholder = True
            result.notes.append("homepage looks like a parked/placeholder/default page")
        if hp is not None and hp.needs_js:
            result.needs_js = True
            if self.renderer is not None:
                html = await self.renderer.render(home.final_url)
                if html:
                    home_page.parsed = parse_html(html, home.final_url)
                    hp = home_page.parsed
                    result.notes.append("homepage rendered with Playwright (JavaScript site)")
            else:
                result.notes.append("site appears to need JavaScript; content may be incomplete "
                                    "(enable Playwright in Settings if installed)")

        if canonicalize(start_url) != canonicalize(root):
            enqueue(start_url, "", 1)
        if hp is not None:
            for link in hp.links:
                enqueue(link.url, link.text, 1)

        high_value = {"about", "contact", "careers", "internship", "services", "team"}
        found_types = {q[3] for q in queue}
        if self.settings.use_sitemaps and len(found_types & high_value) < 3:
            await self._seed_from_sitemap(home.final_url, enqueue, result)
            found_types = {q[3] for q in queue}
        if not ({"about", "contact"} & found_types):
            base = f"{urlsplit(home.final_url).scheme}://{urlsplit(home.final_url).netloc}"
            for path in WELL_KNOWN_FALLBACKS:
                enqueue(base + path, "", 1)

        fetched = 1
        while queue and fetched < budget:
            if is_cancelled():
                result.notes.append("crawl cancelled")
                break
            _neg, _o, url, ptype, depth = heapq.heappop(queue)
            if type_counts.get(ptype, 0) >= TYPE_CAPS.get(ptype, 1):
                continue
            res = await self._fetch(url)
            fetched += 1
            if res.robots_blocked:
                continue
            page = record(res, url, ptype)
            if page.parsed is None or page.is_duplicate:
                continue
            type_counts[ptype] = type_counts.get(ptype, 0) + 1
            if depth < self.settings.max_depth:
                for link in page.parsed.links:
                    lt = classify(link.url, link.text)
                    if lt in ("careers", "internship", "team", "contact", "about"):
                        enqueue(link.url, link.text, depth + 1)
        log_event(log, "site_crawled", logging.DEBUG, site=result.site_domain, pages=len(result.pages), good=len(result.good_pages))
        return result

    async def _seed_from_sitemap(self, home_url: str, enqueue, result: SiteCrawlResult) -> None:
        try:
            policy = await self.fetcher.robots_policy(home_url)
        except Exception:  # pragma: no cover - defensive
            return
        p = urlsplit(home_url)
        candidates = policy.sitemaps()[:2] or [f"{p.scheme}://{p.netloc}/sitemap.xml"]
        for sm in candidates:
            res = await self.fetcher.get(sm, accept=("application/xml", "text/xml", "text/plain"),
                                         max_bytes=2_000_000, retries=0)
            if not res.ok:
                continue
            locs = SITEMAP_LOC.findall(res.text())
            sub = [u for u in locs if u.lower().endswith(".xml")]
            pages = [u for u in locs if not u.lower().endswith(".xml")]
            if not pages and sub:
                # Sitemap index: look at the first "page"/"post" sitemap only.
                pick = next((u for u in sub if "page" in u.lower()), sub[0])
                r2 = await self.fetcher.get(pick, accept=("application/xml", "text/xml", "text/plain"),
                                            max_bytes=2_000_000, retries=0)
                pages = SITEMAP_LOC.findall(r2.text()) if r2.ok else []
            for u in pages[:500]:
                if classify(u) != "other":
                    enqueue(u, "", 1)
            result.notes.append(f"sitemap consulted ({len(pages)} URLs)")
            return
