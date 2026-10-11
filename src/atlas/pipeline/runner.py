"""Run jobs in background threads (Streamlit) or in the foreground (CLI).

Each job runs in its own thread with its own event loop and SQLite connection.
Progress lives in the database, so the UI can poll it and a crashed or stopped
process leaves a resumable job behind (marked 'interrupted' on next start).
"""

from __future__ import annotations

import asyncio
import threading

from atlas.config import AppConfig
from atlas.contacts.verify import DomainMailChecker
from atlas.db import jobs
from atlas.db.core import open_db
from atlas.db.settings import effective_config, load_profile
from atlas.models.search import SearchCriteria
from atlas.net.cache import HttpCache
from atlas.net.fetcher import Fetcher
from atlas.pipeline.assess_company import assess_company, rescore_company
from atlas.pipeline.context import PipelineContext
from atlas.pipeline.orchestrator import run_job
from atlas.pipeline.research import research_company

_threads: dict[str, threading.Thread] = {}
_lock = threading.Lock()


def alive_job_ids() -> set[int]:
    with _lock:
        return {int(k.split(":")[1]) for k, t in _threads.items() if k.startswith("job:") and t.is_alive()}


def is_running(key: str) -> bool:
    with _lock:
        t = _threads.get(key)
        return bool(t and t.is_alive())


def start_job(cfg: AppConfig, job_id: int) -> bool:
    key = f"job:{job_id}"
    with _lock:
        t = _threads.get(key)
        if t and t.is_alive():
            return False
        t = threading.Thread(target=lambda: asyncio.run(run_job(cfg, job_id)), name=f"atlas-{key}", daemon=True)
        _threads[key] = t
        t.start()
    return True


def run_job_blocking(cfg: AppConfig, job_id: int) -> str:
    return asyncio.run(run_job(cfg, job_id))


async def _refresh(cfg: AppConfig, company_id: int, criteria: SearchCriteria, force: bool) -> None:
    conn = open_db(cfg.paths.db_path)
    eff = effective_config(conn, cfg)
    cache = HttpCache(eff.paths.cache_db_path)
    try:
        async with Fetcher(eff.http, cache) as fetcher:
            ctx = PipelineContext(cfg=eff, conn=conn, fetcher=fetcher,
                                  mail_checker=DomainMailChecker(eff.verification.dns_timeout_s),
                                  profile=load_profile(conn))
            await research_company(ctx, company_id, force=force)
            await assess_company(ctx, company_id, criteria)
    finally:
        cache.close()
        conn.close()


def start_company_refresh(cfg: AppConfig, company_id: int, criteria: SearchCriteria, force: bool = True) -> bool:
    key = f"company:{company_id}"
    with _lock:
        t = _threads.get(key)
        if t and t.is_alive():
            return False
        t = threading.Thread(target=lambda: asyncio.run(_refresh(cfg, company_id, criteria, force)),
                             name=f"atlas-{key}", daemon=True)
        _threads[key] = t
        t.start()
    return True


def refresh_company_blocking(cfg: AppConfig, company_id: int, criteria: SearchCriteria, force: bool = True) -> None:
    asyncio.run(_refresh(cfg, company_id, criteria, force))


def rescore_all(cfg: AppConfig) -> int:
    """Recompute rankings for every assessed company (fast; no network)."""
    conn = open_db(cfg.paths.db_path)
    try:
        eff = effective_config(conn, cfg)
        ctx = PipelineContext(cfg=eff, conn=conn, fetcher=None, mail_checker=None, profile=load_profile(conn))  # type: ignore[arg-type]
        ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT company_id FROM qualification_assessments WHERE is_current = 1")]
        n = 0
        for cid in ids:
            if rescore_company(ctx, cid) is not None:
                n += 1
        return n
    finally:
        conn.close()


def recover_stale_jobs(cfg: AppConfig) -> list[int]:
    conn = open_db(cfg.paths.db_path)
    try:
        return jobs.mark_stale_jobs_interrupted(conn, alive_ids=alive_job_ids())
    finally:
        conn.close()
