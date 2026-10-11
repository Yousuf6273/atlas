"""Search-job orchestration: discovery → selection → research → assessment.

Persistent and resumable: every stage records its progress in SQLite. Re-running
a job skips providers that already completed for it and companies whose crawl
finished, so an interrupted job continues where it stopped.

The target count is a goal, not a quota: companies are researched in priority
order until enough are *qualified* or the research budget is exhausted. Quality
thresholds are never lowered to reach the target.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import AsyncExitStack

from atlas.config import AppConfig
from atlas.contacts.verify import DomainMailChecker
from atlas.crawl.render import PlaywrightRenderer, playwright_available
from atlas.db import companies, jobs, research
from atlas.db import providers as provider_db
from atlas.db.core import loads, open_db
from atlas.db.settings import effective_config, load_profile
from atlas.llm.client import OllamaClient
from atlas.logging_setup import get_logger, setup_logging
from atlas.models.enums import JobStatus, QualificationStatus
from atlas.models.search import SearchCriteria
from atlas.net.cache import HttpCache
from atlas.net.fetcher import Fetcher
from atlas.pipeline.assess_company import assess_company
from atlas.pipeline.context import JobCancelled, PipelineContext
from atlas.pipeline.ingest import ingest_candidate
from atlas.pipeline.research import research_company
from atlas.providers import registry
from atlas.providers.base import ProviderContext, ProviderError

log = get_logger("pipeline.orchestrator")


async def _heartbeat(conn: sqlite3.Connection, job_id: int, stop: asyncio.Event) -> None:
    while not stop.is_set():
        jobs.update_progress(conn, job_id)
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except TimeoutError:
            pass


async def stage_discovery(ctx: PipelineContext, criteria: SearchCriteria) -> dict:
    conn = ctx.conn
    done = jobs.completed_providers(conn, ctx.job_id)
    to_run = registry.search_providers(conn, criteria)
    stats = {"providers": {}, "candidates": 0, "new": 0, "merged": 0, "review": 0}
    for i, (prov, skip_reason) in enumerate(to_run, 1):
        ctx.check_cancel()
        name = prov.name
        if name in done:
            ctx.event(f"{name}: already completed for this job (resume)", stage="discovery")
            continue
        if skip_reason:
            jobs.start_provider_run(conn, ctx.job_id, name)
            jobs.finish_provider_run(conn, ctx.job_id, name, status="skipped", notes=skip_reason)
            stats["providers"][name] = {"status": "skipped", "reason": skip_reason}
            continue
        ctx.progress("discovery", i - 1, len(to_run), f"Querying {prov.info.display_name}")
        jobs.start_provider_run(conn, ctx.job_id, name)
        pctx = ProviderContext(cfg=ctx.cfg, fetcher=ctx.fetcher, log=ctx.log, is_cancelled=ctx.cancelled,
                               progress=lambda m: ctx.progress("discovery", message=m))
        try:
            result = await prov.discover(criteria, pctx)
        except ProviderError as exc:
            jobs.finish_provider_run(conn, ctx.job_id, name, status="failed", error=str(exc))
            provider_db.record_run(conn, name, ok=False, error=str(exc))
            ctx.event(f"{prov.info.display_name} failed: {exc}", level="warning", stage="discovery")
            stats["providers"][name] = {"status": "failed", "error": str(exc)}
            continue
        except Exception as exc:  # unexpected provider bug: record, continue with other sources
            jobs.finish_provider_run(conn, ctx.job_id, name, status="failed", error=f"{type(exc).__name__}: {exc}")
            provider_db.record_run(conn, name, ok=False, error=f"{type(exc).__name__}: {exc}")
            ctx.event(f"{prov.info.display_name} crashed: {type(exc).__name__}: {exc}", level="error", stage="discovery")
            stats["providers"][name] = {"status": "failed", "error": str(exc)}
            continue
        counts = {"new": 0, "merged": 0, "known_source": 0, "new_needs_review": 0}
        for cand in result.candidates:  # keep whatever was found, even if cancelled midway
            _cid, action = ingest_candidate(conn, cand, ctx.job_id)
            counts[action] = counts.get(action, 0) + 1
        if ctx.cancelled():
            jobs.finish_provider_run(conn, ctx.job_id, name, status="failed", result_count=len(result.candidates),
                                     error="cancelled before completion — re-runs on resume", partial=True)
            raise JobCancelled()
        notes = "; ".join(result.notes + ([f"partial coverage: {len(result.errors)} sub-queries failed — re-runs on "
                                           f"resume (successful parts are cached)"] if result.partial else []))
        jobs.finish_provider_run(conn, ctx.job_id, name, status="completed", result_count=len(result.candidates),
                                 error="; ".join(result.errors)[:1000] or None, notes=notes[:1000],
                                 partial=result.partial)
        provider_db.record_run(conn, name, ok=True, result_count=len(result.candidates))
        ctx.event(f"{prov.info.display_name}: {len(result.candidates)} candidates "
                  f"({counts['new'] + counts['new_needs_review']} new, {counts['merged']} merged, "
                  f"{counts['known_source']} already known)" + (" — PARTIAL coverage" if result.partial else ""),
                  stage="discovery", level="warning" if result.partial else "info")
        stats["providers"][name] = {"status": "completed", "candidates": len(result.candidates), "partial": result.partial,
                                    **counts}
        stats["candidates"] += len(result.candidates)
    return stats


def _industry_hint(conn: sqlite3.Connection, cid: int, targets: list[str]) -> str:
    for (raw,) in conn.execute("SELECT raw_json FROM company_sources WHERE company_id = ?", (cid,)):
        for hint in (loads(raw, {}) or {}).get("industry_hints", []):
            if not targets or hint in targets:
                return hint
    return "other"


def select_for_research(ctx: PipelineContext, criteria: SearchCriteria) -> list[int]:
    """Order this job's companies for research.

    Within each industry the best candidates come first; across industries the order is round-robin, so
    one plentiful sector (e.g. software start-ups on Wikidata) cannot use up the whole research budget
    before other requested sectors are looked at.
    """
    conn = ctx.conn
    targets = [i.value for i in criteria.industries]
    groups: dict[str, list[tuple[float, int]]] = {}
    for cid in companies.job_company_ids(conn, ctx.job_id):
        c = companies.get_company(conn, cid)
        row = conn.execute("SELECT prefilter_score FROM job_companies WHERE job_id = ? AND company_id = ?",
                           (ctx.job_id, cid)).fetchone()
        s = float(row["prefilter_score"] or 0)
        if c.get("website"):
            s += 0.25
        nsrc = conn.execute("SELECT COUNT(DISTINCT provider) FROM company_sources WHERE company_id = ?", (cid,)).fetchone()[0]
        if nsrc >= 2:
            s += 0.1
        if c.get("qualification_status") == "rejected" and c.get("last_researched_at"):
            s -= 0.5  # already researched and rejected; re-assessed cheaply but not prioritised
        groups.setdefault(_industry_hint(conn, cid, targets), []).append((s, cid))
    for g in groups.values():
        g.sort(key=lambda x: -x[0])
    # Requested industries first (in the order asked), then anything else.
    order = [t for t in targets if t in groups] + sorted(k for k in groups if k not in targets)
    ordered: list[int] = []
    i = 0
    while any(i < len(groups[k]) for k in order):
        for k in order:
            if i < len(groups[k]):
                ordered.append(groups[k][i][1])
        i += 1
    cap = min(ctx.cfg.crawl.max_companies_to_research,
              max(criteria.desired_count, int(criteria.desired_count * ctx.cfg.crawl.research_multiplier)))
    ordered = ordered[:cap]
    companies.set_selected_for_research(conn, ctx.job_id, ordered)
    return ordered


async def stage_research_and_assess(ctx: PipelineContext, criteria: SearchCriteria, ordered: list[int]) -> dict:
    conn = ctx.conn
    sem = asyncio.Semaphore(ctx.cfg.crawl.company_concurrency)
    qualified = 0
    processed = 0
    stats = {"researched": 0, "assessed": 0, "qualified": 0, "needs_review": 0, "rejected": 0, "failed": 0,
             "site_status": {}}
    total = len(ordered)
    done_crawls = {r["company_id"] for r in conn.execute(
        "SELECT company_id FROM crawl_jobs WHERE job_id = ? AND status IN ('done','skipped')", (ctx.job_id,))}

    async def one(cid: int) -> None:
        nonlocal processed, qualified
        async with sem:
            ctx.check_cancel()
            name = companies.get_company(conn, cid)["name"]
            try:
                if cid not in done_crawls:
                    out = await research_company(ctx, cid)
                    stats["site_status"][out.status] = stats["site_status"].get(out.status, 0) + 1
                    stats["researched"] += 1
                a = await assess_company(ctx, cid, criteria)
                stats["assessed"] += 1
                stats[a.status.value] = stats.get(a.status.value, 0) + 1
                if a.status == QualificationStatus.QUALIFIED:
                    qualified += 1
            except JobCancelled:
                raise
            except Exception as exc:
                stats["failed"] += 1
                ctx.event(f"research failed for {name}: {type(exc).__name__}: {exc}", level="error", stage="research")
                log.exception("research failed", extra={"fields": {"company_id": cid}})
            processed += 1
            ctx.progress("research", processed, total, f"Researched {name} · {qualified} qualified so far")

    batch = max(ctx.cfg.crawl.company_concurrency * 2, 4)
    for start in range(0, total, batch):
        ctx.check_cancel()
        if qualified >= criteria.desired_count:
            ctx.event(f"Target of {criteria.desired_count} qualified companies reached; stopping research early",
                      stage="research")
            break
        # TaskGroup cancels sibling tasks if one raises (e.g. cancellation), so nothing
        # keeps running after the job's connection and HTTP client are closed.
        try:
            async with asyncio.TaskGroup() as tg:
                for cid in ordered[start:start + batch]:
                    tg.create_task(one(cid))
        except* JobCancelled:
            raise JobCancelled() from None
    stats["qualified"] = qualified
    return stats


async def run_job(cfg: AppConfig, job_id: int) -> str:
    """Run (or resume) a search job to completion. Returns the final status."""
    setup_logging(cfg.paths.log_dir)
    conn = open_db(cfg.paths.db_path)
    registry.sync_to_db(conn)
    eff = effective_config(conn, cfg)
    criteria = jobs.job_criteria(conn, job_id)
    profile = load_profile(conn)
    jobs.set_status(conn, job_id, JobStatus.RUNNING, message="Starting")
    jobs.add_event(conn, job_id, "Job started" if not jobs.provider_runs(conn, job_id) else "Job resumed", stage="start")
    stop = asyncio.Event()
    hb = asyncio.create_task(_heartbeat(conn, job_id, stop))
    cache = HttpCache(eff.paths.cache_db_path)
    final = JobStatus.FAILED
    try:
        async with AsyncExitStack() as stack:
            fetcher = await stack.enter_async_context(Fetcher(eff.http, cache))
            renderer = None
            if eff.crawl.enable_playwright and playwright_available():
                renderer = await stack.enter_async_context(PlaywrightRenderer(eff.http.user_agent))
            llm = OllamaClient(eff.llm, conn) if eff.llm.enabled else None
            if llm is not None:
                ok, why = await llm.check()
                jobs.add_event(conn, job_id, f"Local AI: {why}", stage="start")
                if not ok:
                    llm = None
            ctx = PipelineContext(cfg=eff, conn=conn, fetcher=fetcher,
                                  mail_checker=DomainMailChecker(eff.verification.dns_timeout_s), profile=profile,
                                  job_id=job_id, llm=llm, renderer=renderer)
            disc = await stage_discovery(ctx, criteria)
            ctx.check_cancel()
            ordered = select_for_research(ctx, criteria)
            ctx.event(f"{len(companies.job_company_ids(conn, job_id))} candidate companies after deduplication; "
                      f"researching up to {len(ordered)} in priority order", stage="selection")
            res = await stage_research_and_assess(ctx, criteria, ordered)
            stats = {"discovery": disc, "research": res, "http": fetcher.stats,
                     "crawl_jobs": research.crawl_status_counts(conn, job_id), "counters": ctx.counters,
                     "llm": llm.stats if llm else None}
            jobs.set_stats(conn, job_id, stats)
            msg = (f"Done: {res['qualified']} qualified of target {criteria.desired_count} "
                   f"({res['assessed']} assessed, {res.get('needs_review', 0)} need review)")
            if res["qualified"] < criteria.desired_count:
                msg += " — fewer than the target; widen the search or import more sources rather than lowering standards"
            jobs.add_event(conn, job_id, msg, stage="done")
            final = JobStatus.COMPLETED
            jobs.set_status(conn, job_id, final, message=msg)
    except JobCancelled:
        final = JobStatus.CANCELLED
        jobs.add_event(conn, job_id, "Cancelled by user; progress is saved and the job can be resumed", stage="cancel")
        jobs.set_status(conn, job_id, final, message="Cancelled — resumable")
    except Exception as exc:
        log.exception("job failed", extra={"fields": {"job_id": job_id}})
        jobs.add_event(conn, job_id, f"Job failed: {type(exc).__name__}: {exc}", level="error")
        jobs.set_status(conn, job_id, JobStatus.FAILED, error=f"{type(exc).__name__}: {exc}", message="Failed — resumable")
    finally:
        stop.set()
        await hb
        cache.close()
        conn.close()
    return final.value
