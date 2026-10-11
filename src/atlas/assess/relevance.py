"""Keyword scanning against the industry taxonomy.

Used for two purposes:
* cheap pre-filtering of discovery candidates (name, tags, short descriptions),
* evidence extraction during qualification (full page text).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from atlas.assess.taxonomy import (
    compiled_industries,
    industries_for_osm_tags,
    industry_for_wikidata_label,
)
from atlas.models.enums import Industry
from atlas.models.search import SearchCriteria


@dataclass(frozen=True)
class TermHit:
    industry: Industry
    strength: str  # strong | medium | negative
    term: str
    start: int
    end: int


def scan(text: str, industries: Iterable[Industry] | None = None) -> list[TermHit]:
    if not text:
        return []
    comp = compiled_industries()
    keys = list(industries) if industries else list(comp)
    hits: list[TermHit] = []
    for k in keys:
        ci = comp[k]
        for strength, patterns in (("strong", ci.strong + ci.strong_cs), ("medium", ci.medium), ("negative", ci.negative)):
            for pat in patterns:
                for m in pat.finditer(text):
                    hits.append(TermHit(k, strength, m.group(0), m.start(), m.end()))
    return _drop_shadowed(hits)


def _drop_shadowed(hits: list[TermHit]) -> list[TermHit]:
    """A negative phrase (e.g. 'car rental') suppresses positive hits it overlaps for the same industry."""
    negs = [h for h in hits if h.strength == "negative"]
    if not negs:
        return hits
    out = []
    for h in hits:
        if h.strength != "negative" and any(
            n.industry == h.industry and n.start <= h.start < n.end for n in negs
        ):
            continue
        out.append(h)
    return out


@dataclass
class QuickRelevance:
    score: float  # 0..1
    industries: list[Industry] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    excluded: bool = False
    exclude_reason: str | None = None


def _kw_pattern(kw: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])", re.IGNORECASE)


def quick_relevance(name: str, description: str = "", *, tags: dict | None = None, labels: Iterable[str] = (),
                    criteria: SearchCriteria | None = None) -> QuickRelevance:
    """Score how likely a raw record matches the requested industries (0..1)."""
    targets = list(criteria.industries) if criteria and criteria.industries else list(Industry)
    excluded = set(criteria.excluded_industries) if criteria else set()
    per: dict[Industry, float] = {}
    reasons: list[str] = []

    def bump(ind: Industry, amount: float, why: str) -> None:
        per[ind] = per.get(ind, 0.0) + amount
        if amount > 0 and len(reasons) < 8:
            reasons.append(why)

    for h in scan(name or ""):
        w = {"strong": 0.5, "medium": 0.2, "negative": -0.5}[h.strength]
        bump(h.industry, w, f"name contains '{h.term}'")
    for h in scan(description or ""):
        w = {"strong": 0.3, "medium": 0.08, "negative": -0.3}[h.strength]
        bump(h.industry, w, f"description mentions '{h.term}'")
    for ind in industries_for_osm_tags({k: v for k, v in (tags or {}).items() if isinstance(v, str)}):
        bump(ind, 0.35, "OpenStreetMap tags indicate " + ind.value)
    for lab in labels:
        for ind in industry_for_wikidata_label(lab):
            bump(ind, 0.45, f"Wikidata industry '{lab}'")

    kw_bonus = 0.0
    if criteria:
        hay = f"{name} {description}".lower()
        for kw in criteria.keywords:
            if _kw_pattern(kw).search(hay):
                kw_bonus += 0.3
                reasons.append(f"matches keyword '{kw}'")
        for kw in criteria.excluded_keywords:
            if _kw_pattern(kw).search(hay):
                return QuickRelevance(0.0, [], reasons, True, f"contains excluded keyword '{kw}'")

    ranked = sorted(per.items(), key=lambda kv: -kv[1])
    if ranked and ranked[0][0] in excluded and ranked[0][1] >= 0.4:
        return QuickRelevance(0.0, [ranked[0][0]], reasons, True, f"primary industry {ranked[0][0].value} is excluded")
    target_score = max((per.get(t, 0.0) for t in targets), default=0.0)
    score = max(0.0, min(1.0, target_score + kw_bonus))
    inds = [k for k, v in ranked if v > 0 and k in targets]
    return QuickRelevance(score, inds, reasons)
