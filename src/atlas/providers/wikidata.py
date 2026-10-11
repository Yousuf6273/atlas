"""Wikidata Query Service (SPARQL).

Access conditions (checked 2026-10-11): free, no key. Wikimedia's robot and
User-Agent policy requires an identifying User-Agent with contact details —
requests without one receive HTTP 403. ATLAS issues one query per search
(cached for 7 days). Coverage skews to larger/notable organisations, which
complements OpenStreetMap's SME coverage. Data is CC0.
"""

from __future__ import annotations

from collections import defaultdict

from atlas.assess.relevance import quick_relevance
from atlas.geo.areas import AREAS
from atlas.geo.geometry import emirate_of, haversine_km, in_bbox
from atlas.models.company import CandidateCompany, CandidateLocation
from atlas.models.enums import LocationType, ProviderAvailability
from atlas.models.search import SearchCriteria
from atlas.providers.base import (
    DiscoveryProvider,
    ProviderContext,
    ProviderError,
    ProviderInfo,
    ProviderResult,
)
from atlas.resolve.normalize import normalize_url
from atlas.timeutil import now_iso

CACHE_TTL_S = 7 * 24 * 3600

# Organisation classes verified against live Wikidata on 2026-10-11.
ORG_TYPES = {
    "Q4830453": "business", "Q783794": "company", "Q6881511": "enterprise", "Q43229": "organization",
    "Q46970": "airline", "Q1058914": "software company", "Q891723": "public company",
    "Q1589009": "privately held company", "Q18388277": "technology company", "Q22687": "bank",
}

SPARQL = """
SELECT ?org ?orgLabel ?orgDescription ?website ?typeLabel ?industryLabel ?hqLabel ?coord ?employees WHERE {
  VALUES ?type { %s }
  ?org wdt:P17 wd:Q878 ; wdt:P31 ?type .
  OPTIONAL { ?org wdt:P856 ?website }
  OPTIONAL { ?org wdt:P452 ?industry }
  OPTIONAL { ?org wdt:P159 ?hq }
  OPTIONAL { ?org wdt:P625 ?coord }
  OPTIONAL { ?org wdt:P1128 ?employees }
  FILTER NOT EXISTS { ?org wdt:P576 ?dissolved }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
""" % " ".join(f"wd:{q}" for q in ORG_TYPES)

# HQ labels Wikidata uses for each emirate (lower-case substrings).
HQ_LABELS = {
    "dubai": ("dubai", "jebel ali", "dubai silicon oasis", "dubai internet city", "difc", "dubai media city",
              "al quoz", "business bay", "dubai south", "dubai investments park"),
    "abu_dhabi": ("abu dhabi", "masdar", "al ain", "mussafah", "al maryah"),
    "sharjah": ("sharjah",),
    "ajman": ("ajman",),
    "ras_al_khaimah": ("ras al khaimah", "ras al-khaimah"),
    "fujairah": ("fujairah",),
    "umm_al_quwain": ("umm al quwain",),
}


def _parse_point(wkt: str | None) -> tuple[float, float] | None:
    # "Point(55.27 25.2)" -> (lat, lon)
    if not wkt or not wkt.startswith("Point("):
        return None
    try:
        lon, lat = wkt[6:-1].split()
        return float(lat), float(lon)
    except ValueError:
        return None


def _in_requested_area(criteria: SearchCriteria, hq_labels: set[str], point: tuple[float, float] | None) -> bool | None:
    """True/False when determinable, None when unknown (kept, decided later)."""
    if not criteria.areas or any(a.kind == "country" for a in criteria.areas):
        return True
    for area in criteria.areas:
        if area.kind == "zone":
            # A zone request needs zone-level evidence; an HQ of just "Dubai" is not enough.
            if point is not None and area.center and area.radius_m and \
                    haversine_km(point[0], point[1], *area.center) * 1000 <= area.radius_m * 1.5:
                return True
            if any(alias in hq for alias in area.aliases if len(alias) > 3 for hq in hq_labels):
                return True
            continue
        root = area if area.kind in ("emirate", "city") else AREAS.get(area.parent_key or "")
        if point is not None:
            if root and root.iso_code and emirate_of(*point) == root.iso_code:
                return True
            if area.bbox and in_bbox(point[0], point[1], area.bbox):
                return True
        if root is not None:
            labels = HQ_LABELS.get(root.key, ())
            if any(any(l in hq for l in labels) for hq in hq_labels):
                return True
    if point is None and not hq_labels:
        return None
    return False


class WikidataProvider(DiscoveryProvider):
    info = ProviderInfo(
        name="wikidata",
        display_name="Wikidata (SPARQL)",
        availability=ProviderAvailability.AVAILABLE,
        description="Organisations with country = UAE, including official websites, industries and HQ locations.",
        coverage="~1,700+ UAE organisations with websites; strongest for larger and well-known companies, "
                 "weak for small private firms.",
        source_url="https://query.wikidata.org/",
        limitations="Community-edited: industry labels and employee counts can be outdated or missing. "
                    "Requires an identifying User-Agent (configured automatically).",
        enabled_by_default=True,
        runs_in_search=True,
    )

    async def _query(self, ctx: ProviderContext) -> list[dict]:
        res = await ctx.fetcher.get(ctx.cfg.discovery.wikidata_endpoint, params={"query": SPARQL, "format": "json"},
                                    headers={"Accept": "application/sparql-results+json"}, check_robots=False,
                                    accept=("application/sparql-results+json", "application/json"),
                                    cache_ttl_s=CACHE_TTL_S, max_bytes=40_000_000, retries=2)
        if not res.ok:
            raise ProviderError(f"Wikidata query failed: {res.error or res.status}")
        try:
            return res.json()["results"]["bindings"]
        except (ValueError, KeyError) as exc:
            raise ProviderError(f"Unexpected Wikidata response: {exc}") from exc

    async def discover(self, criteria: SearchCriteria, ctx: ProviderContext) -> ProviderResult:
        ctx.progress("Querying Wikidata")
        rows = await self._query(ctx)
        grouped: dict[str, dict] = defaultdict(lambda: {"types": set(), "industries": set(), "hq": set(),
                                                         "websites": [], "coords": None, "employees": None})
        for b in rows:
            qid = b["org"]["value"].rsplit("/", 1)[-1]
            g = grouped[qid]
            g["label"] = b.get("orgLabel", {}).get("value", qid)
            g["description"] = b.get("orgDescription", {}).get("value", "")
            if "typeLabel" in b:
                g["types"].add(b["typeLabel"]["value"])
            if "industryLabel" in b:
                g["industries"].add(b["industryLabel"]["value"])
            if "hqLabel" in b:
                g["hq"].add(b["hqLabel"]["value"].lower())
            if "website" in b and b["website"]["value"] not in g["websites"]:
                g["websites"].append(b["website"]["value"])
            if "coord" in b and g["coords"] is None:
                g["coords"] = _parse_point(b["coord"]["value"])
            if "employees" in b:
                try:
                    val = int(float(b["employees"]["value"]))
                    g["employees"] = max(g["employees"] or 0, val)
                except ValueError:
                    pass
        result = ProviderResult(scanned=len(grouped))
        retrieved = now_iso()
        for qid, g in grouped.items():
            label = g.get("label", qid)
            if label == qid:  # no English label
                continue
            in_area = _in_requested_area(criteria, g["hq"], g["coords"])
            if in_area is False:
                continue
            rel = quick_relevance(label, g["description"], labels=sorted(g["industries"]), criteria=criteria)
            if rel.excluded or rel.score <= 0:
                continue
            website = next((normalize_url(w) for w in g["websites"] if normalize_url(w)), None)
            loc = None
            if g["coords"] or g["hq"]:
                loc = CandidateLocation(city=next(iter(sorted(g["hq"])), None), country_code="AE",
                                        latitude=g["coords"][0] if g["coords"] else None,
                                        longitude=g["coords"][1] if g["coords"] else None,
                                        location_type=LocationType.HEADQUARTERS if g["hq"] else LocationType.UNKNOWN)
            result.candidates.append(CandidateCompany(
                name=label, provider=self.name, external_id=qid,
                source_url=f"https://www.wikidata.org/wiki/{qid}", website=website,
                description=g["description"] or None, location=loc,
                industry_hints=[i.value for i in rel.industries],
                tags={"types": sorted(g["types"]), "industries": sorted(g["industries"]), "hq": sorted(g["hq"])},
                employees=g["employees"],
                employees_source=f"Wikidata {qid} (P1128)" if g["employees"] else None,
                retrieved_at=retrieved, relevance_hint=rel.score,
            ))
        result.notes.append(f"{len(grouped)} UAE organisations scanned; {len(result.candidates)} matched industry and area")
        return result

    async def health_check(self, ctx: ProviderContext) -> tuple[bool, str]:
        res = await ctx.fetcher.get(ctx.cfg.discovery.wikidata_endpoint,
                                    params={"query": "ASK { wd:Q612 wdt:P17 wd:Q878 }", "format": "json"},
                                    headers={"Accept": "application/sparql-results+json"}, check_robots=False,
                                    accept=None, use_cache=False, retries=0)
        if res.ok:
            return True, "SPARQL endpoint reachable"
        return False, f"SPARQL endpoint error: {res.error or res.status}"
