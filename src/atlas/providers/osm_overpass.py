"""OpenStreetMap via the public Overpass API.

Access conditions (checked 2026-10-11): free, no key; fair use guideline is
well under 10,000 queries and 1 GB/day per user; clients must send an
identifying User-Agent. Public instances are frequently overloaded (HTTP
429/504), so ATLAS queries small bounding-box tiles sequentially, fails over
between instances, caches tile responses for 3 days and reports tiles that
could not be fetched instead of hiding the gap.

Data © OpenStreetMap contributors, ODbL 1.0.
"""

from __future__ import annotations

import asyncio
import hashlib
import re

from atlas.assess.relevance import quick_relevance
from atlas.geo.areas import AREAS
from atlas.geo.geometry import circle_bbox, tile_bbox
from atlas.logging_setup import get_logger, log_event
from atlas.models.company import CandidateCompany, CandidateLocation
from atlas.models.enums import LocationType, ProviderAvailability
from atlas.models.search import GeoArea, SearchCriteria
from atlas.providers.base import (
    DiscoveryProvider,
    ProviderContext,
    ProviderInfo,
    ProviderResult,
)
from atlas.resolve.normalize import normalize_url
from atlas.timeutil import now_iso

TILE_CACHE_TTL_S = 3 * 24 * 3600
log = get_logger("providers.overpass")

# Feature classes that are never relevant technical employers for this use case.
EXCLUDED_TAGS = {
    "office": {"government", "diplomatic", "estate_agent", "lawyer", "insurance", "travel_agent", "religion",
               "political_party", "notary", "tax_advisor", "educational_institution", "employment_agency",
               "moving_company", "association", "ngo", "charity", "property_management", "visa", "real_estate"},
    "craft": {"tailor", "photographer", "confectionery", "caterer", "jeweller", "upholsterer", "key_cutter",
              "hairdresser", "shoemaker", "beekeeper", "bakery", "dressmaker", "florist", "cleaning", "sweep"},
}

# Infrastructure and facilities that carry office/industrial tags but are not
# employers in their own right (substations, gates, plants of utilities…).
INFRASTRUCTURE_NAME = re.compile(
    r"\b(substation|sub-station|gate\s*\d*|parking|pump(ing)?\s+station|sewage|reservoir|police|metro|"
    r"mosque|masjid|school|clinic|pharmacy|salon|restaurant|cafe|cafeteria|supermarket|grocery|labour camp|"
    r"accommodation|staff housing|warehouse \d+|plot\s*\d+)\b", re.IGNORECASE)

OVERPASS_SELECTORS = (
    'nwr["office"]["name"]',
    'nwr["craft"]["name"]',
    'nwr["industrial"]["name"]',
    'nwr["man_made"="works"]["name"]',
    'nwr["building"~"^(industrial|factory)$"]["name"]',
)


BUSINESS_OFFICE = {"company", "it", "engineer", "engineering", "research", "yes", "consulting", "industrial",
                   "manufacturer", "software", "logistics", "energy_supplier", "telecommunication"}


def _business_like(tags: dict) -> bool:
    return (tags.get("office") in BUSINESS_OFFICE or bool(tags.get("craft")) or bool(tags.get("industrial"))
            or tags.get("man_made") == "works" or tags.get("building") in ("industrial", "factory"))


def build_query(bbox: tuple[float, float, float, float], timeout_s: int) -> str:
    s, w, n, e = bbox
    body = "".join(f"  {sel};\n" for sel in OVERPASS_SELECTORS)
    return f"[out:json][timeout:{timeout_s}][bbox:{s},{w},{n},{e}];\n(\n{body});\nout tags center;"


def _area_bboxes(criteria: SearchCriteria, tile_deg: float, max_tiles: int) -> list[tuple[float, float, float, float]]:
    areas: list[GeoArea] = list(criteria.areas)
    if not areas:
        areas = [AREAS["uae"]]
    boxes: list[tuple[float, float, float, float]] = []
    for area in areas:
        if area.kind == "country":
            # Tile only the populated emirate boxes, not the desert.
            for a in AREAS.values():
                if a.kind == "emirate" and a.bbox:
                    boxes.append(a.bbox)
        elif area.kind == "zone" and area.center and area.radius_m:
            boxes.append(circle_bbox(area.center[0], area.center[1], area.radius_m * 1.15))
        elif area.bbox:
            boxes.append(area.bbox)
    step = tile_deg
    while True:
        tiles = [t for b in boxes for t in (tile_bbox(b, step) if (b[2] - b[0]) > step or (b[3] - b[1]) > step else [b])]
        if len(tiles) <= max_tiles or step > 1.0:
            return tiles
        step *= 1.3


def element_to_candidate(el: dict, retrieved_at: str) -> CandidateCompany | None:
    tags = el.get("tags") or {}
    name = tags.get("name:en") or tags.get("name")
    if not name:
        return None
    for key, bad in EXCLUDED_TAGS.items():
        if tags.get(key) in bad:
            return None
    if INFRASTRUCTURE_NAME.search(name) or tags.get("power") or tags.get("amenity") in {"police", "fire_station"}:
        return None
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    website = normalize_url(tags.get("website") or tags.get("contact:website") or tags.get("url"))
    addr_parts = [tags.get(k) for k in ("addr:unit", "addr:floor", "addr:housename", "addr:housenumber", "addr:street",
                                        "addr:suburb", "addr:district", "addr:city") if tags.get(k)]
    address = tags.get("addr:full") or (", ".join(addr_parts) if addr_parts else None)
    loc = CandidateLocation(address=address, city=tags.get("addr:city"), country_code="AE", latitude=lat,
                            longitude=lon, location_type=LocationType.UNKNOWN)
    keep = {k: v for k, v in tags.items() if k in {"office", "craft", "industrial", "man_made", "building", "company",
                                                    "operator", "brand", "description", "name", "name:en", "name:ar",
                                                    "opening_hours", "check_date", "start_date"}}
    return CandidateCompany(
        name=name.strip(),
        provider="osm_overpass",
        external_id=f"{el['type']}/{el['id']}",
        source_url=f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
        website=website,
        email=(tags.get("email") or tags.get("contact:email") or "").split(";")[0].strip() or None,
        phone=(tags.get("phone") or tags.get("contact:phone") or tags.get("contact:mobile") or "").split(";")[0].strip() or None,
        description=tags.get("description"),
        location=loc,
        tags=keep,
        retrieved_at=retrieved_at,
    )


class _EndpointPool:
    """Rotates endpoints, keeps one request in flight per endpoint and skips endpoints that keep failing."""

    def __init__(self, endpoints: list[str]):
        self.endpoints = endpoints
        self.failures = {e: 0 for e in endpoints}
        self.locks = {e: asyncio.Lock() for e in endpoints}

    def order(self, query: str) -> list[str]:
        offset = int(hashlib.md5(query.encode()).hexdigest(), 16) % max(1, len(self.endpoints))
        rotated = self.endpoints[offset:] + self.endpoints[:offset]
        healthy = [e for e in rotated if self.failures[e] < 3]
        # Circuit breaker: tripped endpoints are tried last, so a recovering server still gets a chance.
        return healthy + [e for e in rotated if e not in healthy]

    def slot(self, endpoint: str) -> asyncio.Lock:
        return self.locks[endpoint]

    def success(self, endpoint: str) -> None:
        self.failures[endpoint] = 0

    def failure(self, endpoint: str) -> None:
        self.failures[endpoint] += 1


class OverpassProvider(DiscoveryProvider):
    info = ProviderInfo(
        name="osm_overpass",
        display_name="OpenStreetMap (Overpass API)",
        availability=ProviderAvailability.AVAILABLE,
        description="Named offices, workshops, industrial sites and factories mapped in OpenStreetMap.",
        coverage="UAE emirates and free zones. Coverage is uneven: many SMEs are unmapped, and only some "
                 "records carry websites or contact details.",
        source_url="https://wiki.openstreetmap.org/wiki/Overpass_API",
        limitations="Public servers are shared and often overloaded; failed tiles are reported as partial "
                    "coverage. Fair-use: small number of tile queries per search, cached for 3 days.",
        enabled_by_default=True,
        runs_in_search=True,
    )

    def applicable(self, criteria: SearchCriteria) -> tuple[bool, str]:
        if criteria.country_code != "AE":
            return False, "only UAE areas are configured"
        return True, ""

    async def _fetch_tile(self, ctx: ProviderContext, bbox: tuple[float, float, float, float],
                          pool: _EndpointPool) -> tuple[list[dict] | None, str | None]:
        d = ctx.cfg.discovery
        query = build_query(bbox, d.overpass_query_timeout_s)
        last_err = None
        for round_ in range(2):
            for ep in pool.order(query):
                if ctx.is_cancelled():
                    return None, "cancelled"
                async with pool.slot(ep):
                    res = await ctx.fetcher.post_form(ep, {"data": query}, accept=("application/json",),
                                                      cache_ttl_s=TILE_CACHE_TTL_S, retries=0)
                if res.ok:
                    try:
                        data = res.json()
                    except ValueError:
                        last_err = f"{ep}: invalid JSON"
                        pool.failure(ep)
                        continue
                    remark = data.get("remark")
                    if remark and "error" in remark.lower():
                        last_err = f"{ep}: {remark[:120]}"
                        pool.failure(ep)
                        continue
                    pool.success(ep)
                    return data.get("elements", []), None
                last_err = f"{ep}: {res.error or res.status}"
                pool.failure(ep)
                log_event(log, "overpass_endpoint_failed", endpoint=ep, error=str(res.error or res.status)[:120])
            await asyncio.sleep(4 * (round_ + 1))
        return None, last_err

    async def discover(self, criteria: SearchCriteria, ctx: ProviderContext) -> ProviderResult:
        d = ctx.cfg.discovery
        tiles = _area_bboxes(criteria, d.overpass_tile_deg, d.overpass_max_tiles)
        result = ProviderResult()
        seen: dict[str, CandidateCompany] = {}
        failed = 0
        done = 0
        retrieved = now_iso()
        pool = _EndpointPool(list(d.overpass_endpoints))
        # One tile in flight per endpoint (public instances allow very few slots per client).
        sem = asyncio.Semaphore(max(1, len(d.overpass_endpoints)))

        async def run_tile(tile):
            nonlocal failed, done
            async with sem:
                if ctx.is_cancelled():
                    return
                elements, err = await self._fetch_tile(ctx, tile, pool)
            done += 1
            ctx.progress(f"OpenStreetMap tiles {done}/{len(tiles)} ({failed} failed)")
            if elements is None:
                failed += 1
                result.errors.append(f"tile {tile}: {err}")
                return
            for el in elements:
                result.scanned += 1
                cand = element_to_candidate(el, retrieved)
                if cand is None or cand.external_id in seen:
                    continue
                rel = quick_relevance(cand.name, cand.description or "", tags=cand.tags, criteria=criteria)
                if rel.excluded:
                    continue
                if rel.score <= 0:
                    # Uninformative names are common ("Strata", "Calidus"). Keep businesses that have a
                    # website and a business-like tag at the lowest priority; research decides.
                    if not (cand.website and _business_like(cand.tags)):
                        continue
                    rel.score = 0.05
                cand.relevance_hint = rel.score
                cand.industry_hints = [i.value for i in rel.industries]
                seen[cand.external_id] = cand

        await asyncio.gather(*(run_tile(t) for t in tiles))
        if ctx.is_cancelled() and done < len(tiles):
            result.notes.append("cancelled before all tiles were queried")
            result.partial = True
        if failed:
            result.partial = True
            result.notes.append(f"{failed} of {len(tiles)} map tiles could not be fetched (server overload); "
                                "re-run later to fill the gap — successful tiles are cached.")
        result.candidates = list(seen.values())
        result.notes.append(f"scanned {result.scanned} named map features in {done - failed}/{len(tiles)} tiles; "
                            f"{len(result.candidates)} matched the requested industries")
        return result

    async def health_check(self, ctx: ProviderContext) -> tuple[bool, str]:
        for ep in ctx.cfg.discovery.overpass_endpoints:
            status_url = ep.replace("/interpreter", "/status")
            res = await ctx.fetcher.get(status_url, check_robots=False, accept=None, use_cache=False, retries=0)
            if res.ok:
                first = res.text().splitlines()[3:5]
                return True, f"{ep} reachable; " + " ".join(first)[:200]
        return False, "no Overpass endpoint reachable"
