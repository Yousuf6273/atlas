"""Company qualification: industry relevance, business type, geography, size, activity.

Industry relevance rubric (0–100 per industry)
---------------------------------------------
raw points, then score = 100·(1 − e^(−raw/40)) (diminishing returns):
* each distinct *strong* taxonomy term on the official site: +14 on home/about/services/products pages, +10 elsewhere
* each distinct *medium* term: +4 (+3 elsewhere)
* each activity statement mentioning the industry: +6
* company name contains a strong term: +20 (medium: +6)
* Wikidata industry label maps to the industry: +25; OpenStreetMap tag hint: +15; imported industry column: +15
* each *negative* look-alike phrase (e.g. "car rental" for automotive): −15

Decision
--------
* Rejected: excluded business type (confident), excluded primary industry, clearly outside the
  requested geography, parked/placeholder website, or relevance < review threshold after the
  site was actually researched (≥ 2 pages).
* Qualified: relevance ≥ qualified threshold, location in/near the requested area, not inactive,
  identity not contradicted, and evidence from the official website or a structured source.
* Needs review: everything else (with the reasons listed).
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date

from atlas.assess.dossier import Dossier
from atlas.assess.relevance import scan
from atlas.assess.taxonomy import (
    SCHEMA_TYPE_HINTS,
    compiled_business_types,
    industries_for_osm_tags,
    industry_for_wikidata_label,
)
from atlas.config import QualificationSettings
from atlas.geo.areas import AREAS, NEIGHBOURS, find_areas, root_emirate
from atlas.geo.geometry import emirate_of, haversine_km, in_bbox
from atlas.models.assessment import IndustryScore, QualificationResult
from atlas.models.company import Evidence
from atlas.models.enums import (
    BUSINESS_TYPE_LABELS,
    BusinessType,
    GeoStatus,
    Industry,
    OperationalStatus,
    QualificationStatus,
    SizeBand,
)
from atlas.models.search import SearchCriteria

CORE_PAGES = {"home", "about", "services", "products", "industries"}


def size_band_for(n: int) -> SizeBand:
    if n < 10:
        return SizeBand.MICRO
    if n < 50:
        return SizeBand.SMALL
    if n < 250:
        return SizeBand.MEDIUM
    if n < 1000:
        return SizeBand.LARGE
    return SizeBand.ENTERPRISE


def _ev(claim: dict) -> Evidence:
    return Evidence(source_url=claim["source_url"], snippet=claim["evidence_snippet"], retrieved_at=claim["retrieved_at"],
                    webpage_id=claim.get("webpage_id"))


# ------------------------------------------------------------------ industry

def industry_scores(d: Dossier) -> list[IndustryScore]:
    raw: dict[Industry, float] = defaultdict(float)
    evidence: dict[Industry, list[Evidence]] = defaultdict(list)
    terms: dict[Industry, list[str]] = defaultdict(list)
    # Each distinct term counts once per industry (template text repeated on every page must not
    # inflate the score); it gets the higher "core page" weight if it appears on any core page.
    best_weight: dict[tuple[Industry, str], float] = {}
    for c in d.claims_of("industry_signal", "industry_negative"):
        try:
            ind_s, strength, term = c["value"].split("|", 2)
            ind = Industry(ind_s)
        except ValueError:
            continue
        core = d.page_type_of(c["source_url"]) in CORE_PAGES
        w = {"strong": 14 if core else 10, "medium": 4 if core else 3, "negative": -15}.get(strength, 0)
        key = (ind, f"{strength}:{term}")
        if key not in best_weight or abs(w) > abs(best_weight[key]):
            best_weight[key] = w
        if strength != "negative" and term not in terms[ind]:
            terms[ind].append(term)
            if len(evidence[ind]) < 4 and strength == "strong":
                evidence[ind].append(_ev(c))
    for (ind, _term), w in best_weight.items():
        raw[ind] += w
    seen_statements: set[str] = set()
    for c in d.claims_of("activity", "offering"):
        key = c["value"].lower()[:200]
        if key in seen_statements:
            continue
        seen_statements.add(key)
        for ind in {h.industry for h in scan(c["value"]) if h.strength != "negative"}:
            raw[ind] += 6
            if len(evidence[ind]) < 4:
                evidence[ind].append(_ev(c))
    for h in scan(d.name):
        raw[h.industry] += {"strong": 20, "medium": 6, "negative": -15}[h.strength]
        if h.strength != "negative":
            terms[h.industry].append(f"name: {h.term.lower()}")
    for src in d.sources:
        from atlas.db.core import loads

        raw_rec = loads(src.get("raw_json"), {}) or {}
        tags = raw_rec.get("tags") or {}
        if src["provider"] == "wikidata":
            for lab in [x for x in tags.get("industries", []) if x.lower() not in ("industry", "business", "company")]:
                for ind in industry_for_wikidata_label(lab):
                    raw[ind] += 25
                    terms[ind].append(f"wikidata: {lab}")
                    evidence[ind].append(Evidence(source_url=src["source_url"] or "", snippet=f"Wikidata industry: {lab}",
                                                  retrieved_at=src["retrieved_at"]))
        elif src["provider"] == "osm_overpass":
            for ind in industries_for_osm_tags({k: v for k, v in tags.items() if isinstance(v, str)}):
                raw[ind] += 15
                terms[ind].append("osm tag")
        elif src["provider"] == "manual_csv" and tags.get("industry_text"):
            for ind in {h.industry for h in scan(tags["industry_text"]) if h.strength != "negative"}:
                raw[ind] += 15
                terms[ind].append(f"imported: {tags['industry_text'][:40]}")
    strong_found = {ind for (ind, t) in best_weight if t.startswith("strong:")}
    strong_found |= {h.industry for h in scan(d.name) if h.strength == "strong"}
    strong_found |= {ind for ind, ts in terms.items() if any(t.startswith("wikidata:") for t in ts)}
    out = []
    for ind in Industry:
        r = raw.get(ind, 0.0)
        score = 100 * (1 - math.exp(-max(r, 0) / 40))
        if ind not in strong_found:
            score = min(score, 40.0)  # generic words alone ("industries", "production") cannot qualify a company
        out.append(IndustryScore(industry=ind.value, score=round(score, 1), evidence=evidence.get(ind, [])[:4],
                                 matched_terms=terms.get(ind, [])[:12]))
    return sorted(out, key=lambda s: -s.score)


# ------------------------------------------------------------------ business type

def business_type(d: Dossier) -> tuple[BusinessType, list[Evidence], float, bool]:
    """(type, evidence, confidence score, ambiguous)."""
    scores: dict[BusinessType, float] = defaultdict(float)
    ev: dict[BusinessType, list[Evidence]] = defaultdict(list)
    seen: set[str] = set()
    per_snippet: dict[tuple[BusinessType, str], float] = {}
    for c in d.claims_of("business_type_signal"):
        key, _, term = c["value"].partition("|")
        if c["value"] in seen:
            continue  # the same phrase on many pages (e.g. a footer) counts once
        seen.add(c["value"])
        try:
            bt = BusinessType(key)
        except ValueError:
            continue
        weight = next((b.weight for b, _p, _n in compiled_business_types() if b.key == bt), 1.0)
        w = weight * (0.5 if term.startswith("~") else 1.0)
        # Several patterns matching one sentence are one piece of evidence.
        sk = (bt, (c["evidence_snippet"] or "")[:200].lower())
        if w > per_snippet.get(sk, 0):
            scores[bt] += w - per_snippet.get(sk, 0)
            per_snippet[sk] = w
            ev[bt].append(_ev(c))
    for c in d.claims_of("structured_type"):
        for t in c["value"].split(","):
            hint = SCHEMA_TYPE_HINTS.get(t.strip())
            if hint and f"schema:{t}" not in seen:
                seen.add(f"schema:{t}")
                scores[hint[0]] += hint[1]
                ev[hint[0]].append(_ev(c))
    if re.search(r"\bgeneral trading\b", d.name, re.IGNORECASE):
        # UAE trade-licence wording in the legal name is strong self-description.
        scores[BusinessType.DISTRIBUTOR_TRADING] += 1.5
        ev[BusinessType.DISTRIBUTOR_TRADING].append(Evidence(source_url=d.company.get("website") or "",
                                                             snippet=f"legal name: {d.name}"))
    for bdef, _pats, name_pats in compiled_business_types():
        for pat in name_pats:
            if pat.search(d.name):
                scores[bdef.key] += 0.8 * bdef.weight
                ev[bdef.key].append(Evidence(source_url=d.company.get("website") or "", snippet=f"company name: {d.name}"))
                break
    # Types that are easily triggered by mentions of clients, investors or partners need
    # at least two distinct signals (or a name match) before they are assigned.
    for bt in (BusinessType.GOVERNMENT_PUBLIC, BusinessType.FINANCIAL_FIRM, BusinessType.OPERATOR,
               BusinessType.RETAIL_CONSUMER, BusinessType.CONSULTANCY, BusinessType.RECRUITMENT_AGENCY,
               BusinessType.TRAINING_PROVIDER):
        if 0 < scores.get(bt, 0) < 1.5:
            del scores[bt]
    if not scores:
        return BusinessType.UNKNOWN, [], 0.0, False
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top, top_score = ranked[0]
    if top_score < 0.8:
        # Only weak hints (each worth ≤ 0.65): say "unknown" rather than guess. One strong
        # self-description of any type (weight ≥ 0.8) is enough.
        return BusinessType.UNKNOWN, [], top_score, False
    # A manufacturer that also distributes is a manufacturer — but only with strong manufacturing evidence
    # comparable to the distribution evidence (not a line of company history).
    mfg = scores.get(BusinessType.MANUFACTURER, 0)
    if top == BusinessType.DISTRIBUTOR_TRADING and mfg >= 2.0 and mfg >= 0.6 * top_score:
        top, top_score = BusinessType.MANUFACTURER, mfg
    ambiguous = len(ranked) > 1 and ranked[1][1] >= 1.0 and abs(ranked[0][1] - ranked[1][1]) < 0.3
    return top, ev[top][:3], top_score, ambiguous


# ------------------------------------------------------------------ geography

def geography(d: Dossier, criteria: SearchCriteria) -> tuple[GeoStatus, list[str]]:
    notes: list[str] = []
    coords = [(loc["latitude"], loc["longitude"], loc["source_provider"]) for loc in d.locations
              if loc["latitude"] is not None and loc["longitude"] is not None]
    mentions = {c["value"] for c in d.claims_of("location_mention")}
    # Structured locations (Wikidata HQ city, OSM addr:city, JSON-LD address, imports) count as mentions.
    structured_text = [loc.get("city") or "" for loc in d.locations] + [loc.get("address") or "" for loc in d.locations]
    structured_text += [c["value"] for c in d.claims_of("structured_address")]
    for t in structured_text:
        for a, _alias in find_areas(t):
            mentions.add(a.key)
    uae_text = bool(d.claims_of("uae_presence")) or any(
        (loc.get("country_code") or "").upper() == "AE" for loc in d.locations)
    emirates_from_coords = {emirate_of(lat, lon) for lat, lon, _ in coords} - {None}
    emirates_from_mentions = set()
    for key in mentions:
        a = AREAS.get(key)
        if a is not None:
            root = root_emirate(a)
            if root is not None and root.iso_code:
                emirates_from_mentions.add(root.iso_code)
    for lat, lon, prov in coords:
        notes.append(f"coordinates {lat:.4f},{lon:.4f} from {prov} → {emirate_of(lat, lon) or 'outside UAE'}")
    if mentions:
        notes.append("website mentions: " + ", ".join(sorted(AREAS[m].label for m in mentions if m in AREAS)))

    targets = [a for a in criteria.areas if a.kind != "country"]
    if not targets:
        if emirates_from_coords or emirates_from_mentions or uae_text:
            return GeoStatus.IN_AREA if criteria.areas else GeoStatus.IN_COUNTRY, notes + ["UAE presence evidenced"]
        if coords:
            return GeoStatus.OUTSIDE, notes + ["coordinates outside the UAE"]
        return GeoStatus.UNKNOWN, notes + ["no location evidence found"]

    for area in targets:
        root = root_emirate(area)
        if area.kind == "zone" and area.center and area.radius_m:
            for lat, lon, _ in coords:
                if haversine_km(lat, lon, *area.center) * 1000 <= area.radius_m * 1.25:
                    return GeoStatus.IN_AREA, notes + [f"within ~{area.radius_m / 1000:.1f} km of {area.label}"]
            if area.key in mentions:
                return GeoStatus.IN_AREA, notes + [f"website mentions {area.label}"]
        else:
            if root and root.iso_code in emirates_from_coords:
                return GeoStatus.IN_AREA, notes + [f"coordinates inside {root.label}"]
            if area.bbox and any(in_bbox(lat, lon, area.bbox) for lat, lon, _ in coords) and not emirates_from_coords:
                return GeoStatus.IN_AREA, notes + [f"coordinates inside the {area.label} search box"]
            if root and root.iso_code in emirates_from_mentions:
                return GeoStatus.IN_AREA, notes + [f"website mentions a location in {root.label}"]
    target_emirates = {root_emirate(a).iso_code for a in targets if root_emirate(a)}
    found = emirates_from_coords | emirates_from_mentions
    if found & target_emirates:
        return GeoStatus.NEARBY, notes + ["in the same emirate, but not confirmed inside the requested zone"]
    if any(found & NEIGHBOURS.get(t, set()) for t in target_emirates):
        return GeoStatus.NEARBY, notes + ["located in a neighbouring emirate"]
    if found or uae_text:
        return GeoStatus.IN_COUNTRY, notes + ["UAE presence evidenced, but not in the requested area"]
    if coords and not emirates_from_coords:
        return GeoStatus.OUTSIDE, notes + ["coordinates outside the UAE"]
    return GeoStatus.UNKNOWN, notes + ["no location evidence found (not treated as outside)"]


# ------------------------------------------------------------------ size

def company_size(d: Dossier) -> tuple[SizeBand, str | None]:
    for src in d.sources:
        from atlas.db.core import loads

        rec = loads(src.get("raw_json"), {}) or {}
        if rec.get("employees"):
            n = int(rec["employees"])
            return size_band_for(n), f"{rec.get('employees_source') or src['provider']}: about {n:,} employees"
    best: tuple[int, str] | None = None
    for c in d.claims_of("size_statement"):
        try:
            label, n_s = c["value"].rsplit("|", 1)
            n = int(n_s)
        except ValueError:
            continue
        if best is None or n > best[0]:
            best = (n, f"website: “{c['evidence_snippet'][:140]}”")
    if best:
        return size_band_for(best[0]), best[1] + " (lower bound if it counts only engineers/specialists)"
    return SizeBand.UNKNOWN, None


# ------------------------------------------------------------------ operational status

def operational_status(d: Dossier, today: date | None = None) -> tuple[OperationalStatus, list[str]]:
    today = today or date.today()
    pos: list[str] = []
    neg: list[str] = []
    status = d.site_status()
    words = sum(len(p.text.split()) for p in d.good_pages)
    if status == "placeholder":
        return OperationalStatus.LIKELY_INACTIVE, ["website is a parked/placeholder page"]
    if d.good_pages and words >= 150:
        pos.append(f"official website reachable with substantive content ({len(d.good_pages)} pages)")
    elif status in ("unreachable",):
        neg.append("website unreachable at research time (could be temporary)")
    elif status in ("bot_challenge", "robots_blocked", "robots_unavailable"):
        neg.append("website could not be researched (access restricted); activity not assessable")
    years = [int(c["value"]) for c in d.claims_of("copyright_year") if c["value"].isdigit()]
    if years and max(years) >= today.year - 1:
        pos.append(f"copyright notice {max(years)}")
    dates = [c["value"] for c in d.claims_of("dated_content")]
    if dates:
        latest = max(dates)
        try:
            age_days = (today - date.fromisoformat(latest)).days
            if age_days <= 548:
                pos.append(f"dated content from {latest}")
        except ValueError:
            pass
    if d.claims_of("hiring", "hiring_role"):
        pos.append("currently advertises careers/vacancies")
    if len(d.providers()) >= 2:
        pos.append(f"listed in {len(d.providers())} independent sources")
    for c in d.claims_of("legal_entity"):
        if "ACTIVE" in c["value"]:
            pos.append("active legal entity record (GLEIF)")
    if len(pos) >= 2:
        return OperationalStatus.LIKELY_ACTIVE, pos + neg
    return OperationalStatus.UNCERTAIN, pos + neg


# ------------------------------------------------------------------ decision

def qualify(d: Dossier, criteria: SearchCriteria, settings: QualificationSettings) -> QualificationResult:
    scores = industry_scores(d)
    by_key = {s.industry: s for s in scores}
    targets = [i.value for i in criteria.industries] or [i.value for i in Industry]
    target_best = max((by_key[t] for t in targets), key=lambda s: s.score)
    primary = scores[0] if scores and scores[0].score >= 25 else None
    btype, bt_ev, bt_conf, bt_ambiguous = business_type(d)
    geo, geo_notes = geography(d, criteria)
    size, size_ev = company_size(d)
    op, op_notes = operational_status(d)
    eng_team = bool(d.claims_of("engineering_team")) or any(
        "engineer" in c["value"].lower() for c in d.claims_of("hiring_role"))
    researched = len(d.good_pages)
    identity = d.company.get("identity_status")
    reasons: list[str] = []
    uncertainties: list[str] = []

    relevance = target_best.score
    reasons.append(f"Relevance to {target_best.industry.replace('_', ' ')}: {relevance:.0f}/100 "
                   f"(terms: {', '.join(target_best.matched_terms[:6]) or 'none'})")
    if btype != BusinessType.UNKNOWN:
        reasons.append(f"Business type: {BUSINESS_TYPE_LABELS[btype.value]}")
    if bt_ambiguous:
        uncertainties.append("business type is ambiguous (several types evidenced)")
    if size == SizeBand.UNKNOWN:
        uncertainties.append("company size unknown (no public employee figure found)")
    if geo == GeoStatus.UNKNOWN:
        uncertainties.append("location not evidenced")
    if researched == 0:
        uncertainties.append("official website not researched (" + (d.site_status() or "no website") + ")")

    status = QualificationStatus.NEEDS_REVIEW
    excluded_bt = btype in criteria.excluded_business_types and bt_conf >= 1.2
    excluded_ind = primary is not None and Industry(primary.industry) in criteria.excluded_industries \
        and primary.score >= 50 and primary.score > relevance
    if excluded_bt:
        status = QualificationStatus.REJECTED
        reasons.append(f"Rejected: {BUSINESS_TYPE_LABELS[btype.value].lower()} (excluded type)")
    elif excluded_ind:
        status = QualificationStatus.REJECTED
        reasons.append(f"Rejected: primary industry {primary.industry} is excluded")
    elif geo == GeoStatus.OUTSIDE:
        status = QualificationStatus.REJECTED
        reasons.append("Rejected: evidence places the company outside the UAE")
    elif op == OperationalStatus.LIKELY_INACTIVE:
        status = QualificationStatus.REJECTED
        reasons.append("Rejected: website is parked or a placeholder")
    elif researched >= 2 and relevance < settings.min_relevance_review:
        status = QualificationStatus.REJECTED
        reasons.append(f"Rejected: website researched ({researched} pages) but relevance only {relevance:.0f}")
    else:
        zone_only = bool(criteria.areas) and all(a.kind == "zone" for a in criteria.areas)
        geo_ok = geo == GeoStatus.IN_AREA or (geo == GeoStatus.NEARBY and not zone_only) or (
            geo == GeoStatus.IN_COUNTRY and (not criteria.areas or any(a.kind == "country" for a in criteria.areas)))
        has_official = researched >= 1
        threshold = settings.min_relevance_qualified + (10 if btype == BusinessType.DISTRIBUTOR_TRADING else 0)
        blockers = []
        if relevance < threshold:
            blockers.append(f"relevance {relevance:.0f} below {threshold:.0f}")
        if not geo_ok:
            blockers.append(f"location status '{geo.value}'")
        if not has_official:
            blockers.append("no official website content researched")
        if identity == "mismatch":
            blockers.append("website identity does not match the company name")
        if criteria.require_engineering_team and not eng_team:
            blockers.append("no evidence of an in-house engineering team")
        if not blockers:
            status = QualificationStatus.QUALIFIED
            reasons.append("Qualified: relevant, located in/near the requested area, evidence from the official website")
        else:
            reasons.append("Needs review: " + "; ".join(blockers))
    if btype == BusinessType.DISTRIBUTOR_TRADING and status != QualificationStatus.REJECTED:
        reasons.append("Note: appears to be a distributor/trading company — less design work, but supplier and "
                       "product-data tasks may fit")

    return QualificationResult(
        status=status, target_relevance=round(relevance, 1),
        primary_industry=primary.industry if primary else None, industry_scores=scores[:5],
        business_type=btype, business_type_evidence=bt_ev, geo_status=geo, geo_evidence=geo_notes,
        size_band=size, size_evidence=size_ev, operational_status=op, operational_indicators=op_notes,
        has_engineering_team_evidence=eng_team, reasons=reasons, uncertainties=uncertainties,
    )
