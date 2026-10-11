"""Entity resolution: decide whether a candidate is an existing company.

Deterministic rules on strong identifiers (website domain, phone) combined with
fuzzy name similarity and geographic distance. Ambiguous cases are never merged
automatically — they create a new record plus a review-queue item.

Decision rules (first match wins):

=====================================================  ==========
Evidence                                                Action
=====================================================  ==========
same domain & name similarity ≥ 60                      merge
same domain & name similarity < 60                      review (possible parent / subsidiary / shared host)
same phone & name similarity ≥ 70                       merge
different company domains & name similarity ≥ 92        review (possible group companies / regional entities)
no conflicting domains & name ≥ 93 & distance ≤ 0.5 km  merge
no conflicting domains & name ≥ 93 & distance unknown   merge if one side lacks a domain *and* no branch marker,
                                                        otherwise review
name ≥ 93 & distance > 5 km                             review (possible branch office)
one name contains the other + distinctive extra tokens  review (possible parent/subsidiary)
name 85–93                                              review
otherwise                                               new company
=====================================================  ==========
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from atlas.assess.taxonomy import GENERIC_NAME_TOKENS
from atlas.geo.geometry import haversine_km
from atlas.models.company import CandidateCompany
from atlas.resolve.normalize import (
    has_branch_marker,
    is_company_domain,
    name_tokens,
    normalize_name,
    normalize_phone,
    registrable_domain,
)


@dataclass
class CandidateFeatures:
    name: str
    norm: str
    tokens: list[str]
    domain: str | None
    phones: list[str]
    coords: list[tuple[float, float]]
    branch: bool

    @classmethod
    def of(cls, cand: CandidateCompany) -> CandidateFeatures:
        dom = registrable_domain(cand.website) if cand.website else None
        phone = normalize_phone(cand.phone) if cand.phone else None
        coords = []
        if cand.location and cand.location.latitude is not None and cand.location.longitude is not None:
            coords.append((cand.location.latitude, cand.location.longitude))
        return cls(name=cand.name, norm=normalize_name(cand.name), tokens=name_tokens(cand.name),
                   domain=dom if is_company_domain(dom) else None, phones=[phone] if phone else [], coords=coords,
                   branch=has_branch_marker(cand.name))


@dataclass
class MatchDecision:
    action: str  # merge | review | new
    company_id: int | None = None
    score: float = 0.0
    reason: str = ""
    relationship: str | None = None  # same_entity | possible_branch | possible_parent_subsidiary | similar_name
    reviews: list[tuple[int, float, str, str]] = field(default_factory=list)  # (company_id, score, reason, rel)


def name_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return max(fuzz.token_sort_ratio(a, b), fuzz.ratio(a, b))


def _distinctive(tokens: list[str]) -> set[str]:
    return {t for t in tokens if t not in GENERIC_NAME_TOKENS and len(t) > 1}


def _min_distance_km(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float | None:
    if not a or not b:
        return None
    return min(haversine_km(x[0], x[1], y[0], y[1]) for x in a for y in b)


def compare(f: CandidateFeatures, row: dict[str, Any]) -> tuple[str, float, str, str | None]:
    """Compare candidate features to an existing company row → (action, score, reason, relationship)."""
    row_norm = row.get("normalized_name") or normalize_name(row.get("name", ""))
    sim = name_similarity(f.norm, row_norm)
    row_domain = row.get("domain")
    row_phones = row.get("phones") or []
    row_coords = row.get("coords") or []
    dist = _min_distance_km(f.coords, row_coords)

    if f.domain and row_domain and f.domain == row_domain:
        if sim >= 60:
            return "merge", sim, f"same website domain ({f.domain}) and similar name ({sim:.0f})", "same_entity"
        return "review", sim, f"same website domain ({f.domain}) but different names ({sim:.0f})", \
            "possible_parent_subsidiary"
    if f.phones and set(f.phones) & set(row_phones) and sim >= 70:
        return "merge", sim, f"same phone number and similar name ({sim:.0f})", "same_entity"
    conflicting_domains = bool(f.domain and row_domain and f.domain != row_domain)
    if conflicting_domains:
        if sim >= 92:
            return "review", sim, (f"near-identical names ({sim:.0f}) but different websites "
                                   f"({f.domain} vs {row_domain})"), "possible_parent_subsidiary"
        return "new", sim, "different websites", None
    if sim >= 93:
        if dist is not None and dist <= 0.5:
            return "merge", sim, f"near-identical names ({sim:.0f}) {dist * 1000:.0f} m apart", "same_entity"
        if dist is not None and dist > 5:
            return "review", sim, f"near-identical names ({sim:.0f}) but {dist:.1f} km apart", "possible_branch"
        if f.branch or has_branch_marker(row.get("name", "")):
            return "review", sim, "near-identical names; one record is marked as a branch", "possible_branch"
        if dist is None and (f.domain is None or row_domain is None):
            return "merge", sim, f"near-identical names ({sim:.0f}), no conflicting identifiers", "same_entity"
        return "review", sim, f"near-identical names ({sim:.0f}), location unclear", "similar_name"
    a, b = _distinctive(f.tokens), _distinctive((row_norm or "").split())
    if a and b and (a < b or b < a) and min(len(a), len(b)) >= 1:
        return "review", sim, "one name contains the other with extra distinctive words", "possible_parent_subsidiary"
    if sim >= 85:
        return "review", sim, f"similar names ({sim:.0f})", "similar_name"
    return "new", sim, "", None


def decide(f: CandidateFeatures, pool: list[dict[str, Any]]) -> MatchDecision:
    merges: list[tuple[float, int, str, str]] = []
    reviews: list[tuple[int, float, str, str]] = []
    for row in pool:
        action, score, reason, rel = compare(f, row)
        if action == "merge":
            merges.append((score, int(row["id"]), reason, rel or "same_entity"))
        elif action == "review":
            reviews.append((int(row["id"]), score, reason, rel or "similar_name"))
    if len(merges) == 1:
        score, cid, reason, rel = merges[0]
        return MatchDecision("merge", cid, score, reason, rel)
    if len(merges) > 1:
        # Ambiguous: candidate matches several existing companies.
        merges.sort(reverse=True)
        for score, cid, reason, rel in merges:
            reviews.append((cid, score, f"ambiguous: matches several records — {reason}", rel))
        return MatchDecision("review", None, merges[0][0], "candidate matches several existing companies",
                             "similar_name", reviews)
    if reviews:
        best = max(reviews, key=lambda r: r[1])
        return MatchDecision("review", None, best[1], best[2], best[3], reviews)
    return MatchDecision("new")
