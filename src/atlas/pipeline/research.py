"""Research one company: website resolution, crawl, extraction, identity, legal entity."""

from __future__ import annotations

from dataclasses import dataclass

from atlas.assess.dossier import load_dossier
from atlas.crawl.site_crawler import SiteCrawler
from atlas.db import companies, contacts, research
from atlas.db.companies import SOURCE_CONFIDENCE
from atlas.extract.contacts import extract_contacts
from atlas.extract.signals import extract_claims, extract_structured
from atlas.models.company import Claim, Evidence
from atlas.pipeline.context import PipelineContext
from atlas.pipeline.identity import identity_status, resolve_website, site_names
from atlas.providers.gleif import lookup_legal_entity
from atlas.resolve.normalize import normalize_name, registrable_domain, website_root
from atlas.timeutil import now_iso, older_than

NAME_FROM_DOMAIN_PROVIDERS = {"url_list", "directory_page", "searxng"}


@dataclass
class ResearchOutcome:
    company_id: int
    status: str  # researched | skipped_fresh | no_website | unreachable | robots_blocked | bot_challenge | failed
    pages: int = 0
    note: str = ""


def _status_claim(company_id: int, status: str, url: str | None, note: str) -> Claim:
    return Claim(claim_type="site_status", value=status,
                 evidence=Evidence(source_url=url or f"atlas://company/{company_id}", snippet=note[:400] or status,
                                   retrieved_at=now_iso()),
                 extractor="rule:crawler", confidence=0.9)


async def research_company(ctx: PipelineContext, company_id: int, *, force: bool = False) -> ResearchOutcome:
    conn, cfg = ctx.conn, ctx.cfg
    company = companies.get_company(conn, company_id)
    if company is None:
        return ResearchOutcome(company_id, "failed", note="company missing")
    cj = research.ensure_crawl_job(conn, company_id, ctx.job_id, cfg.crawl.max_pages_per_site)
    if not force and company.get("last_researched_at") and not older_than(company["last_researched_at"],
                                                                            days=cfg.crawl.page_refresh_days):
        research.update_crawl_job(conn, cj["id"], status="skipped", notes="researched recently; reused")
        return ResearchOutcome(company_id, "skipped_fresh", note="recent research reused")
    research.update_crawl_job(conn, cj["id"], status="running")

    # ---- website resolution for companies without a website -----------------
    if not company.get("website"):
        if cfg.discovery.website_resolution and ctx.websites_resolved < cfg.discovery.website_resolution_max:
            ctx.websites_resolved += 1
            url, note = await resolve_website(ctx.fetcher, company["name"])
            if url:
                companies.set_field(conn, company_id, "website", url, confidence=SOURCE_CONFIDENCE["website_resolution"],
                                    source="website_resolution")
                companies.set_field(conn, company_id, "domain", registrable_domain(url),
                                    confidence=SOURCE_CONFIDENCE["website_resolution"], source="website_resolution")
                research.save_claims(conn, company_id, [Claim(
                    claim_type="website_resolution", value=url,
                    evidence=Evidence(source_url=url, snippet=note, retrieved_at=now_iso()),
                    extractor="rule:website_resolution", confidence=0.6)])
                company = companies.get_company(conn, company_id)
                ctx.bump("websites_resolved")
            else:
                research.save_claims(conn, company_id, [_status_claim(company_id, "no_website", None,
                                                                      f"No website on record; resolution: {note}")])
        if not company.get("website"):
            companies.set_identity(conn, company_id, "no_website", "no website found")
            research.update_crawl_job(conn, cj["id"], status="done", pages_fetched=0, notes="no website")
            companies.mark_researched(conn, company_id)
            return ResearchOutcome(company_id, "no_website")

    # ---- crawl ------------------------------------------------------------------
    crawler = SiteCrawler(ctx.fetcher, cfg.crawl, ctx.renderer)
    try:
        crawl = await crawler.crawl(company["website"], is_cancelled=ctx.cancelled)
    except Exception as exc:  # recoverable: record and move on
        research.update_crawl_job(conn, cj["id"], status="failed", error=f"{type(exc).__name__}: {exc}")
        ctx.event(f"crawl failed for {company['name']}", level="warning", stage="research", error=str(exc)[:200])
        return ResearchOutcome(company_id, "failed", note=str(exc)[:200])

    research.retire_claims(conn, company_id, "rule:", keep=("rule:website_resolution",))
    conn.execute("UPDATE contacts SET is_current = 0 WHERE company_id = ? AND source_type = 'official_site'",
                 (company_id,))
    site_domain = crawl.site_domain
    homepage = None
    for page in crawl.pages:
        parsed = page.parsed
        pid = research.save_webpage(
            conn, company_id, url=page.url, final_url=page.final_url, canonical_url=parsed.canonical if parsed else None,
            status_code=page.status, content_type=page.content_type, page_type=page.page_type,
            title=parsed.title if parsed else None, content_hash=parsed.content_hash if parsed else None,
            text_content=parsed.full_text if parsed else None, word_count=parsed.word_count if parsed else 0,
            lang=parsed.lang if parsed else None, needs_js=bool(parsed and parsed.needs_js),
            is_duplicate=page.is_duplicate, error=page.error, fetched_at=page.fetched_at)
        if parsed is None or page.is_duplicate or page.error:
            continue
        if page.page_type == "home" and homepage is None:
            homepage = parsed
        claims = extract_claims(parsed, page.page_type, page.fetched_at, company_name=company["name"])
        claims += extract_structured(parsed, page.fetched_at)
        for c in claims:
            c.evidence.webpage_id = pid
        research.save_claims(conn, company_id, claims)
        for cc in extract_contacts(parsed, page.page_type, site_domain, page.fetched_at):
            contacts.upsert_contact(conn, company_id, cc)

    if crawl.robots_blocked:
        status = "robots_blocked"
    elif crawl.robots_unavailable:
        status = "robots_unavailable"
    elif crawl.bot_challenge:
        status = "bot_challenge"
    elif crawl.placeholder:
        status = "placeholder"
    elif not crawl.reachable:
        status = "unreachable"
    else:
        status = "needs_js" if crawl.needs_js and len(crawl.good_pages) <= 1 else "ok"
    note = "; ".join(crawl.notes + crawl.errors)[:400]
    research.save_claims(conn, company_id, [_status_claim(company_id, status, website_root(company["website"]), note)])
    if crawl.redirected_to_other_domain:
        new_root = f"https://{crawl.redirected_to_other_domain}/"
        companies.set_field(conn, company_id, "website", new_root, confidence=SOURCE_CONFIDENCE["official_site"],
                            source="official_site")
        companies.set_field(conn, company_id, "domain", crawl.redirected_to_other_domain,
                            confidence=SOURCE_CONFIDENCE["official_site"], source="official_site")

    # ---- identity --------------------------------------------------------------
    if homepage is not None:
        structured = [c["value"] for c in research.company_claims(conn, company_id, "structured_name")]
        names = site_names(homepage.title, homepage.og_site_name, structured, homepage.footer_text)
        providers = {s["provider"] for s in companies.company_sources(conn, company_id)}
        name_from_domain = providers and providers <= NAME_FROM_DOMAIN_PROVIDERS and names
        if name_from_domain:
            better = (structured[:1] or ([homepage.og_site_name] if homepage.og_site_name else []) or names)[0]
            if better and normalize_name(better):
                companies.set_field(conn, company_id, "name", better.strip()[:120],
                                    confidence=SOURCE_CONFIDENCE["official_site"], source="official_site")
                conn.execute("UPDATE companies SET normalized_name = ? WHERE id = ?", (normalize_name(better), company_id))
                companies.set_identity(conn, company_id, "verified", f"name taken from the website (“{better}”)")
        else:
            st, why = identity_status(company["name"], names, site_domain)
            companies.set_identity(conn, company_id, st, why)
    elif status in ("unreachable", "robots_blocked", "robots_unavailable", "bot_challenge"):
        companies.set_identity(conn, company_id, "unverified", f"website not researchable ({status})")

    # ---- legal entity (GLEIF) — identity cross-check, cached ---------------------
    if len(normalize_name(company["name"])) >= 4:
        try:
            m = await lookup_legal_entity(ctx.fetcher, cfg.discovery.gleif_endpoint, company["name"])
        except Exception:  # network trouble must not fail research
            m = None
        if m is not None:
            research.save_claims(conn, company_id, [Claim(
                claim_type="legal_entity", value=f"LEI {m.lei} · {m.legal_name} · entity {m.entity_status} · "
                                                f"registration {m.registration_status} · {m.city or ''}",
                evidence=Evidence(source_url=m.source_url, snippet=f"GLEIF record: {m.legal_name} ({m.city}, {m.region}); "
                                  f"name similarity {m.similarity:.0f}", retrieved_at=now_iso()),
                extractor="rule:gleif", confidence=0.8)])

    # ---- optional local-AI activity summary (quotes verified) ----------------------
    if ctx.llm is not None and cfg.llm.use_for_company_summary and status == "ok":
        from atlas.llm.tasks import llm_company_claims

        llm_claims, flags = await llm_company_claims(ctx.llm, load_dossier(conn, company_id))
        if llm_claims:
            research.retire_claims(conn, company_id, "llm:")
            research.save_claims(conn, company_id, llm_claims)
        if flags:
            ctx.event(f"possible prompt-injection text on {company['name']}'s site (ignored)", level="warning",
                      stage="research", flags=flags[:3])

    research.update_crawl_job(conn, cj["id"], status="done", pages_fetched=len(crawl.pages), notes=note)
    companies.mark_researched(conn, company_id)
    return ResearchOutcome(company_id, "researched" if status == "ok" else status, len(crawl.good_pages), note)
