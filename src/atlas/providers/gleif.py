"""GLEIF LEI records (Global Legal Entity Identifier Foundation).

Access conditions (checked 2026-10-11): free public REST API, no key, data
licensed CC0; documented rate limit 60 requests/minute. UAE coverage is small
(only entities that obtained an LEI) and records carry no websites, so GLEIF is
used primarily for *identity verification* (does a registered legal entity
with this name exist, and is its registration active?). Discovery via GLEIF is
available but disabled by default because its yield is low.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from atlas.assess.relevance import quick_relevance
from atlas.geo.areas import AREAS
from atlas.models.company import CandidateCompany, CandidateLocation
from atlas.models.enums import LocationType, ProviderAvailability
from atlas.models.search import SearchCriteria
from atlas.net.fetcher import Fetcher
from atlas.providers.base import (
    DiscoveryProvider,
    ProviderContext,
    ProviderInfo,
    ProviderResult,
)
from atlas.resolve.normalize import normalize_name
from atlas.timeutil import now_iso

CACHE_TTL_S = 7 * 24 * 3600
ACCEPT = ("application/vnd.api+json", "application/json")


@dataclass
class LegalEntityMatch:
    lei: str
    legal_name: str
    matched_name: str
    similarity: float
    entity_status: str
    registration_status: str
    city: str | None
    region: str | None
    source_url: str


def _names(attrs: dict) -> list[str]:
    e = attrs.get("entity", {})
    names = [e.get("legalName", {}).get("name", "")]
    for key in ("otherNames", "transliteratedOtherNames"):
        for n in e.get(key) or []:
            if n.get("name"):
                names.append(n["name"])
    return [n for n in names if n]


async def lookup_legal_entity(fetcher: Fetcher, endpoint: str, company_name: str, country: str = "AE",
                              min_similarity: float = 90.0) -> LegalEntityMatch | None:
    """Best LEI record whose (English or transliterated) name matches ``company_name``."""
    norm = normalize_name(company_name)
    if len(norm) < 3:
        return None
    res = await fetcher.get(f"{endpoint}/lei-records",
                            params={"filter[fulltext]": norm, "filter[entity.legalAddress.country]": country,
                                    "page[size]": "10"},
                            headers={"Accept": "application/vnd.api+json"}, check_robots=False, accept=ACCEPT,
                            cache_ttl_s=CACHE_TTL_S, retries=1)
    if not res.ok:
        return None
    try:
        data = res.json().get("data", [])
    except ValueError:
        return None
    best: LegalEntityMatch | None = None
    for rec in data:
        attrs = rec.get("attributes", {})
        for n in _names(attrs):
            sim = fuzz.token_sort_ratio(norm, normalize_name(n))
            if sim >= min_similarity and (best is None or sim > best.similarity):
                e = attrs.get("entity", {})
                addr = e.get("legalAddress", {})
                best = LegalEntityMatch(
                    lei=attrs.get("lei", rec.get("id", "")), legal_name=e.get("legalName", {}).get("name", n),
                    matched_name=n, similarity=sim, entity_status=e.get("status", "UNKNOWN"),
                    registration_status=attrs.get("registration", {}).get("status", "UNKNOWN"),
                    city=addr.get("city"), region=addr.get("region"),
                    source_url=f"https://search.gleif.org/#/record/{attrs.get('lei', rec.get('id', ''))}",
                )
    return best


EMIRATE_REGION = {a.key: a.iso_code for a in AREAS.values() if a.kind == "emirate"}


class GleifProvider(DiscoveryProvider):
    info = ProviderInfo(
        name="gleif",
        display_name="GLEIF legal entity register",
        availability=ProviderAvailability.AVAILABLE,
        description="Registered legal entities (LEI) in the UAE; used to verify legal identity and status.",
        coverage="Only UAE entities that hold an LEI (a few thousand, mostly larger or finance-linked firms). "
                 "Many names are recorded in Arabic only.",
        source_url="https://www.gleif.org/en/lei-data/gleif-api",
        limitations="No websites or industries in records; discovery yield is low, so it is disabled for "
                    "discovery by default and used for identity checks instead. 60 requests/minute limit.",
        enabled_by_default=False,
        runs_in_search=True,
    )

    async def discover(self, criteria: SearchCriteria, ctx: ProviderContext) -> ProviderResult:
        from atlas.assess.taxonomy import INDUSTRIES

        terms: list[str] = []
        for ind in criteria.industries or []:
            terms += [s for s in INDUSTRIES[ind].query_synonyms if len(s) > 4][:3]
        terms += criteria.keywords[:3]
        terms = list(dict.fromkeys(terms))[:8] or ["engineering", "technologies", "robotics", "aerospace"]
        regions = {EMIRATE_REGION.get(a.key) or EMIRATE_REGION.get(a.parent_key or "") for a in criteria.areas}
        regions.discard(None)
        result = ProviderResult()
        seen: set[str] = set()
        retrieved = now_iso()
        for term in terms:
            if ctx.is_cancelled():
                break
            ctx.progress(f"GLEIF search: {term}")
            res = await ctx.fetcher.get(f"{ctx.cfg.discovery.gleif_endpoint}/lei-records",
                                        params={"filter[fulltext]": term, "filter[entity.legalAddress.country]": "AE",
                                                "page[size]": "100"},
                                        headers={"Accept": "application/vnd.api+json"}, check_robots=False,
                                        accept=ACCEPT, cache_ttl_s=CACHE_TTL_S, retries=1)
            if not res.ok:
                result.errors.append(f"{term}: {res.error or res.status}")
                result.partial = True
                continue
            for rec in res.json().get("data", []):
                attrs = rec.get("attributes", {})
                e = attrs.get("entity", {})
                result.scanned += 1
                lei = attrs.get("lei", rec.get("id"))
                if not lei or lei in seen or e.get("status") != "ACTIVE":
                    continue
                addr = e.get("headquartersAddress") or e.get("legalAddress") or {}
                if regions and addr.get("region") not in regions:
                    continue
                english = next((n for n in _names(attrs) if n.isascii()), None)
                if not english:
                    continue  # Arabic-only names cannot be matched to websites reliably
                rel = quick_relevance(english, criteria=criteria)
                if rel.excluded or rel.score <= 0:
                    continue
                seen.add(lei)
                result.candidates.append(CandidateCompany(
                    name=english, provider=self.name, external_id=lei,
                    source_url=f"https://search.gleif.org/#/record/{lei}",
                    location=CandidateLocation(address=", ".join(addr.get("addressLines") or []) or None,
                                               city=addr.get("city"), country_code="AE",
                                               location_type=LocationType.REGISTERED),
                    tags={"lei": lei, "registration_status": attrs.get("registration", {}).get("status")},
                    industry_hints=[i.value for i in rel.industries], retrieved_at=retrieved,
                    relevance_hint=rel.score,
                ))
        result.notes.append(f"{result.scanned} LEI records scanned; {len(result.candidates)} English-named matches")
        return result

    async def health_check(self, ctx: ProviderContext) -> tuple[bool, str]:
        res = await ctx.fetcher.get(f"{ctx.cfg.discovery.gleif_endpoint}/lei-records", params={"page[size]": "1"},
                                    headers={"Accept": "application/vnd.api+json"}, check_robots=False,
                                    accept=None, use_cache=False, retries=0)
        return (True, "API reachable") if res.ok else (False, f"API error: {res.error or res.status}")


def similarity(a: str, b: str) -> float:
    return fuzz.token_sort_ratio(normalize_name(a), normalize_name(b))
