"""Optional web search through a *self-hosted* SearXNG instance.

Disabled unless ``discovery.searxng_url`` is set. ATLAS does not use public
SearXNG instances or undocumented search-engine endpoints. Your instance must
enable the JSON output format (``search.formats: [html, json]`` in
settings.yml). Results are only website *leads*: each is verified during
research before being treated as a company.

Status: implemented and tested against mocked responses only (no instance was
available during development).
"""

from __future__ import annotations

from atlas.assess.taxonomy import INDUSTRIES
from atlas.models.company import CandidateCompany
from atlas.models.enums import ProviderAvailability
from atlas.models.search import SearchCriteria
from atlas.providers.base import (
    DiscoveryProvider,
    ProviderContext,
    ProviderInfo,
    ProviderResult,
)
from atlas.resolve.normalize import is_company_domain, registrable_domain, website_root
from atlas.timeutil import now_iso

# Aggregators, directories, news and job boards are not company websites.
SKIP_DOMAINS = {
    "glassdoor.com", "indeed.com", "bayt.com", "naukrigulf.com", "gulftalent.com", "zawya.com", "gulfnews.com",
    "khaleejtimes.com", "thenationalnews.com", "arabianbusiness.com", "crunchbase.com", "zoominfo.com",
    "dnb.com", "kompass.com", "yellowpages-uae.com", "connect.ae", "2gis.ae", "tripadvisor.com", "clutch.co",
    "goodfirms.co", "f6s.com", "tracxn.com", "pitchbook.com", "emis.com", "wamda.com", "magnitt.com",
}


class SearxngProvider(DiscoveryProvider):
    info = ProviderInfo(
        name="searxng",
        display_name="Web search (self-hosted SearXNG)",
        availability=ProviderAvailability.REQUIRES_SETUP,
        description="Finds company websites via your own SearXNG metasearch instance.",
        coverage="Depends on the engines your instance queries.",
        source_url="https://docs.searxng.org/",
        limitations="Requires running SearXNG locally (e.g. via Docker) with JSON output enabled. Tested with "
                    "mocked responses only.",
        enabled_by_default=False,
        runs_in_search=True,
    )

    def applicable(self, criteria: SearchCriteria) -> tuple[bool, str]:
        return True, ""

    def queries(self, criteria: SearchCriteria) -> list[str]:
        place = criteria.areas[0].label if criteria.areas else "UAE"
        qs = []
        for ind in criteria.industries or []:
            syn = INDUSTRIES[ind].query_synonyms
            qs.append(f"{syn[0]} company {place}")
            if len(syn) > 2:
                qs.append(f"{syn[2]} {place} engineering")
        for kw in criteria.keywords[:3]:
            qs.append(f"{kw} company {place}")
        return list(dict.fromkeys(qs))[:8] or [f"engineering company {place}"]

    async def discover(self, criteria: SearchCriteria, ctx: ProviderContext) -> ProviderResult:
        base = (ctx.cfg.discovery.searxng_url or "").rstrip("/")
        result = ProviderResult()
        if not base:
            result.errors.append("searxng_url is not configured")
            return result
        seen: set[str] = set()
        retrieved = now_iso()
        for q in self.queries(criteria):
            if ctx.is_cancelled():
                break
            ctx.progress(f"SearXNG: {q}")
            res = await ctx.fetcher.get(f"{base}/search", params={"q": q, "format": "json", "language": "en"},
                                        check_robots=False, accept=("application/json",), cache_ttl_s=24 * 3600,
                                        retries=1)
            if not res.ok:
                result.errors.append(f"{q}: {res.error or res.status}")
                result.partial = True
                continue
            for item in res.json().get("results", [])[:30]:
                result.scanned += 1
                url = item.get("url")
                dom = registrable_domain(url)
                if not dom or dom in seen or dom in SKIP_DOMAINS or not is_company_domain(dom):
                    continue
                seen.add(dom)
                title = (item.get("title") or dom).split("|")[0].split(" - ")[0].strip()[:120]
                result.candidates.append(CandidateCompany(
                    name=title or dom, provider=self.name, external_id=dom, website=website_root(url),
                    source_url=url, description=(item.get("content") or "")[:500] or None,
                    tags={"query": q, "name_from_search_title": True}, retrieved_at=retrieved, relevance_hint=0.3))
        result.notes.append(f"{len(result.candidates)} distinct website leads (unverified until researched)")
        return result

    async def health_check(self, ctx: ProviderContext) -> tuple[bool, str]:
        base = (ctx.cfg.discovery.searxng_url or "").rstrip("/")
        if not base:
            return False, "not configured (set discovery.searxng_url)"
        res = await ctx.fetcher.get(f"{base}/search", params={"q": "test", "format": "json"}, check_robots=False,
                                    accept=None, use_cache=False, retries=0)
        if res.ok:
            return True, "instance reachable with JSON output"
        return False, f"instance error: {res.error or res.status} (is the JSON format enabled?)"
