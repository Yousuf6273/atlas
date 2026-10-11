"""LLM-assisted steps. Each one is optional and has a deterministic fallback."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from atlas.assess.dossier import Dossier
from atlas.geo.areas import AREAS
from atlas.llm.client import OllamaClient
from atlas.llm.guard import (
    SYSTEM_PREAMBLE,
    introduces_new_facts,
    quote_in_source,
    sanitize_untrusted,
    wrap_untrusted,
)
from atlas.logging_setup import get_logger, log_event
from atlas.models.company import Claim, Evidence
from atlas.models.enums import BusinessType, Industry, SizeBand
from atlas.models.search import ParsedQuery

log = get_logger("llm.tasks")

IndustryLit = Literal[tuple(i.value for i in Industry)]  # type: ignore[valid-type]
SizeLit = Literal[tuple(s.value for s in SizeBand if s != SizeBand.UNKNOWN)]  # type: ignore[valid-type]


# ------------------------------------------------------------------ query parsing

class LLMQuery(BaseModel):
    industries: list[IndustryLit] = Field(default_factory=list)
    excluded_industries: list[IndustryLit] = Field(default_factory=list)
    area_keys: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list, max_length=10)
    size_preference: list[SizeLit] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list, max_length=10)
    task_preferences: list[str] = Field(default_factory=list, max_length=10)
    desired_count: int | None = Field(None, ge=1, le=1000)
    require_engineering_team: bool = False


async def llm_refine_query(client: OllamaClient, parsed: ParsedQuery) -> ParsedQuery:
    """Fill gaps in a deterministic parse. Explicit deterministic constraints always win."""
    query, _flags = sanitize_untrusted(parsed.criteria.raw_query, 1000)
    prompt = (
        "Convert this internship-search request into structured filters.\n"
        f"Allowed industries: {[i.value for i in Industry]}\n"
        f"Allowed area_keys: {sorted(AREAS)}\n"
        f"Allowed size_preference: {[s.value for s in SizeBand if s != SizeBand.UNKNOWN]}\n"
        "Only include values clearly implied by the request. Request:\n" + wrap_untrusted("user query", query)
    )
    out = await client.structured(system=SYSTEM_PREAMBLE, prompt=prompt, schema=LLMQuery)
    if out is None:
        return parsed
    c = parsed.criteria.model_copy(deep=True)
    added: list[str] = []
    if not c.industries and out.industries:
        c.industries = [Industry(i) for i in out.industries]
        added.append("industries")
    if not c.excluded_industries and out.excluded_industries:
        c.excluded_industries = [Industry(i) for i in out.excluded_industries]
        added.append("excluded industries")
    if not c.areas:
        valid = [AREAS[k] for k in out.area_keys if k in AREAS]
        if valid:
            c.areas = valid
            added.append("areas")
    if not c.size_preference and out.size_preference:
        c.size_preference = [SizeBand(s) for s in out.size_preference]
        added.append("size")
    for field in ("keywords", "skills", "task_preferences"):
        if not getattr(c, field) and getattr(out, field):
            setattr(c, field, [s.lower()[:40] for s in getattr(out, field)])
            added.append(field)
    if out.require_engineering_team and not c.require_engineering_team:
        c.require_engineering_team = True
        added.append("engineering-team requirement")
    if not added:
        return parsed
    return parsed.model_copy(update={
        "criteria": c, "method": "deterministic+llm",
        "notes": parsed.notes + [f"Local AI suggested: {', '.join(added)} (please confirm below)"]})


# ------------------------------------------------------------------ company activity summary

class QuotedStatement(BaseModel):
    statement: str = Field(..., max_length=300)
    quote: str = Field(..., max_length=400)
    source_url: str


class CompanySummary(BaseModel):
    activities: list[QuotedStatement] = Field(default_factory=list, max_length=6)
    products_services: list[QuotedStatement] = Field(default_factory=list, max_length=8)
    business_type: Literal[tuple(b.value for b in BusinessType)] | None = None  # type: ignore[valid-type]


async def llm_company_claims(client: OllamaClient, d: Dossier, max_chars: int = 9000) -> tuple[list[Claim], list[str]]:
    """Activity claims whose quotes are verified verbatim against the fetched pages."""
    pages = [p for p in d.good_pages if p.page_type in ("home", "about", "services", "products")][:4]
    if not pages:
        return [], []
    parts, all_flags = [], []
    budget = max_chars // max(1, len(pages))
    for p in pages:
        text, flags = sanitize_untrusted(p.text, budget)
        all_flags += flags
        parts.append(wrap_untrusted(p.url, text))
    if all_flags:
        log_event(log, "prompt_injection_flags", company=d.id, flags=all_flags[:5])
    prompt = (f"Company: {d.name}\nFrom the website text below, list what the company does and its main products or "
              "services. For each item give a short neutral statement, a verbatim quote from the text that supports "
              "it, and the source URL shown on the data block.\n\n" + "\n\n".join(parts))
    out = await client.structured(system=SYSTEM_PREAMBLE, prompt=prompt, schema=CompanySummary)
    if out is None:
        return [], all_flags
    by_url = {p.url: p for p in pages}
    claims: list[Claim] = []
    dropped = 0
    for kind, items in (("activity", out.activities), ("offering", out.products_services)):
        for it in items:
            page = by_url.get(it.source_url) or next((p for p in pages if quote_in_source(it.quote, p.text)), None)
            if page is None or not quote_in_source(it.quote, page.text):
                dropped += 1
                continue
            claims.append(Claim(
                claim_type=kind, value=it.statement if kind == "activity" else it.statement[:80],
                evidence=Evidence(source_url=page.url, snippet=it.quote, retrieved_at=page.fetched_at, webpage_id=page.id),
                extractor=f"llm:{client.settings.model}", confidence=0.65))
    if dropped:
        log_event(log, "llm_unsupported_claims_dropped", company=d.id, dropped=dropped)
    return claims, all_flags


# ------------------------------------------------------------------ draft polishing

class PolishedDraft(BaseModel):
    body: str = Field(..., max_length=4000)


async def llm_polish_draft(client: OllamaClient, body: str, facts_text: str) -> tuple[str | None, list[str]]:
    """Rephrase for flow only. Rejected if it introduces any new number or proper noun."""
    prompt = ("Lightly improve the flow of this email written by a 17-year-old A-level student. Keep it natural, "
              "concise and modest. Do not add any facts, skills, numbers, names or claims that are not already "
              "present. Keep placeholders in [brackets] unchanged.\n\n" + wrap_untrusted("draft", body))
    out = await client.structured(system=SYSTEM_PREAMBLE, prompt=prompt, schema=PolishedDraft)
    if out is None:
        return None, ["local AI unavailable"]
    new = introduces_new_facts(body, out.body, facts_text)
    if new:
        return None, [f"rejected AI rewrite: introduced unsupported terms {new[:5]}"]
    return out.body, []
