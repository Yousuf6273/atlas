"""Command-line interface.

    atlas init                      create the database, register providers
    atlas providers [--check]       list discovery providers (optionally run health checks)
    atlas parse "<query>"           show how a query is interpreted
    atlas search "<query>" [--count N] [--yes]   run a search job in the foreground
    atlas resume <job_id>           resume an interrupted/cancelled/failed job
    atlas jobs                      list recent jobs
    atlas top [--job ID] [-n 20]    show the ranked prospects
    atlas export --format xlsx|csv [--job ID] [--out PATH]
    atlas rescore                   recompute rankings after changing weights
    atlas reassess --job ID [--recrawl]   re-run extraction/assessments (page cache makes this fast)
    atlas ui                        start the Streamlit dashboard
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import subprocess
import sys
from pathlib import Path

from atlas.config import PROJECT_ROOT, load_config
from atlas.db import jobs
from atlas.db.core import open_db
from atlas.db.settings import effective_config
from atlas.logging_setup import setup_logging


def _conn(cfg):
    return open_db(cfg.paths.db_path)


def cmd_init(cfg, _args) -> int:
    from atlas.providers import registry

    conn = _conn(cfg)
    registry.sync_to_db(conn)
    print(f"Database ready at {cfg.paths.db_path}")
    return 0


def cmd_providers(cfg, args) -> int:
    from atlas.db import providers as pdb
    from atlas.net.cache import HttpCache
    from atlas.net.fetcher import Fetcher
    from atlas.providers import registry
    from atlas.providers.base import ProviderContext

    conn = _conn(cfg)
    registry.sync_to_db(conn)
    for p in pdb.list_providers(conn):
        print(f"- {p['display_name']} [{p['name']}] availability={p['availability']} enabled={bool(p['enabled'])}")
        print(f"    last success: {p['last_success_at'] or 'never'} · last results: {p['last_result_count']} · "
              f"last error: {p['last_error'] or '-'}")
    if args.check:
        eff = effective_config(conn, cfg)

        async def run():
            async with Fetcher(eff.http, HttpCache(eff.paths.cache_db_path)) as f:
                ctx = ProviderContext(cfg=eff, fetcher=f, log=logging.getLogger("atlas.cli"))
                for prov in registry.IMPLEMENTED:
                    if not prov.info.runs_in_search:
                        continue
                    ok, detail = await prov.health_check(ctx)
                    pdb.record_health(conn, prov.name, ("OK: " if ok else "FAIL: ") + detail)
                    print(f"  health {prov.name}: {'OK' if ok else 'FAIL'} — {detail}")

        asyncio.run(run())
    return 0


def cmd_parse(cfg, args) -> int:
    from atlas.query import parse_query

    p = parse_query(args.query)
    print(p.criteria.model_dump_json(indent=2, exclude={"areas"}))
    print("areas:", [a.label for a in p.criteria.areas])
    for n in p.notes:
        print("note:", n)
    return 0


def _run_with_signal(cfg, job_id: int) -> int:
    from atlas.pipeline.runner import run_job_blocking

    conn = _conn(cfg)

    def handler(_sig, _frm):
        print("\nCancelling… (progress is saved; resume with `atlas resume", job_id, "`)")
        jobs.request_cancel(conn, job_id)

    signal.signal(signal.SIGINT, handler)
    status = run_job_blocking(cfg, job_id)
    job = jobs.get_job(conn, job_id)
    print(f"Job {job_id}: {status} — {job['message']}")
    return 0 if status == "completed" else 1


def cmd_search(cfg, args) -> int:
    from atlas.db.settings import get_setting
    from atlas.query import parse_query

    conn = _conn(cfg)
    p = parse_query(args.query, default_area=get_setting(conn, "default_area", "dubai"))
    if args.count:
        p.criteria.desired_count = args.count
    print("Interpreted as:", p.criteria.summary())
    for n in p.notes:
        print("  note:", n)
    if not args.yes:
        if input("Run this search? [y/N] ").strip().lower() != "y":
            return 1
    job_id = jobs.create_job(conn, p)
    print(f"Job {job_id} created. Ctrl-C cancels safely.")
    return _run_with_signal(cfg, job_id)


def cmd_resume(cfg, args) -> int:
    return _run_with_signal(cfg, args.job_id)


def cmd_jobs(cfg, _args) -> int:
    for j in jobs.list_jobs(_conn(cfg), 20):
        print(f"#{j['id']} {j['status']:<11} {j['created_at']}  {j['query_text'][:60]!r}  — {j['message'] or ''}")
    return 0


def cmd_top(cfg, args) -> int:
    from atlas.db.queries import prospect_rows

    rows = [r for r in prospect_rows(_conn(cfg), job_id=args.job) if r["total_score"] is not None][: args.n]
    for r in rows:
        print(f"{r['total_score']:5.1f}  {r['qualification_status'] or '-':<12} {r['name'][:40]:<40} "
              f"{(r['primary_industry'] or '-'):<14} {r['best_contact'] or '(no channel)'}")
    return 0


def cmd_export(cfg, args) -> int:
    from atlas.export.exporter import export_rows, to_csv_bytes, to_xlsx_bytes

    df = export_rows(_conn(cfg), job_id=args.job)
    out = Path(args.out) if args.out else cfg.paths.export_dir / f"atlas_export{'_job' + str(args.job) if args.job else ''}.{args.format}"
    out.write_bytes(to_xlsx_bytes(df) if args.format == "xlsx" else to_csv_bytes(df))
    print(f"Wrote {len(df)} rows to {out}")
    return 0


def cmd_reassess(cfg, args) -> int:
    """Re-extract evidence (from the page cache where fresh) and re-run assessments for a job's companies."""
    import asyncio as _asyncio

    from atlas.contacts.verify import DomainMailChecker
    from atlas.db import companies as cdb
    from atlas.db.settings import load_profile
    from atlas.net.cache import HttpCache
    from atlas.net.fetcher import Fetcher
    from atlas.pipeline.assess_company import assess_company
    from atlas.pipeline.context import PipelineContext
    from atlas.pipeline.research import research_company

    conn = _conn(cfg)
    eff = effective_config(conn, cfg)
    crit = jobs.job_criteria(conn, args.job)
    ids = [r[0] for r in conn.execute(
        "SELECT company_id FROM crawl_jobs WHERE job_id = ? AND status IN ('done','skipped')", (args.job,))]

    async def run():
        async with Fetcher(eff.http, HttpCache(eff.paths.cache_db_path)) as f:
            ctx = PipelineContext(cfg=eff, conn=conn, fetcher=f, mail_checker=DomainMailChecker(), profile=load_profile(conn),
                                  job_id=args.job)
            sem = _asyncio.Semaphore(eff.crawl.company_concurrency)

            async def one(i, cid):
                async with sem:
                    if cdb.get_company(conn, cid)["merged_into_id"]:
                        return
                    if args.recrawl:
                        await research_company(ctx, cid, force=True)
                    a = await assess_company(ctx, cid, crit)
                    print(f"[{i}/{len(ids)}] {a.status.value:<12} {a.total:5.1f}  {cdb.get_company(conn, cid)['name'][:50]}",
                          flush=True)

            await _asyncio.gather(*(one(i, cid) for i, cid in enumerate(ids, 1)))

    _asyncio.run(run())
    return 0


def cmd_rescore(cfg, _args) -> int:
    from atlas.pipeline.runner import rescore_all

    print(f"Rescored {rescore_all(cfg)} companies")
    return 0


def _port_busy(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _streamlit_healthy(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2) as r:
            return r.read().strip() == b"ok"
    except Exception:
        return False


def cmd_ui(_cfg, args) -> int:
    import time
    import webbrowser

    port = args.port
    if _port_busy(port):
        if _streamlit_healthy(port):
            url = f"http://localhost:{port}"
            print(f"A dashboard is already running at {url} (most likely ATLAS) — opening it.\n"
                  f"To start a second one instead: atlas ui --port {port + 1}")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        free = next((p for p in range(port + 1, port + 50) if not _port_busy(p)), None)
        if free is None:
            print(f"Port {port} and the next 49 ports are busy; choose one with --port.")
            return 1
        print(f"Port {port} is used by another program; starting on port {free} instead.", flush=True)
        port = free
    app = PROJECT_ROOT / "app" / "streamlit_app.py"
    # Run from the project root so .streamlit/config.toml (theme) applies wherever `atlas ui` is called from.
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(port),
                             "--server.headless", "true", "--server.address", "localhost",
                             "--browser.gatherUsageStats", "false"], cwd=PROJECT_ROOT)
    url = f"http://localhost:{port}"
    try:
        for _ in range(120):  # wait up to ~60 s for the server to come up
            if proc.poll() is not None:
                return proc.returncode
            if _streamlit_healthy(port):
                print(f"\nATLAS is running at {url}  — press Ctrl-C to stop.", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
                break
            time.sleep(0.5)
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="atlas", description="ATLAS internship intelligence platform")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p = sub.add_parser("providers")
    p.add_argument("--check", action="store_true")
    p = sub.add_parser("parse")
    p.add_argument("query")
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--count", type=int)
    p.add_argument("--yes", "-y", action="store_true")
    p = sub.add_parser("resume")
    p.add_argument("job_id", type=int)
    sub.add_parser("jobs")
    p = sub.add_parser("top")
    p.add_argument("--job", type=int)
    p.add_argument("-n", type=int, default=25)
    p = sub.add_parser("export")
    p.add_argument("--format", choices=["csv", "xlsx"], default="xlsx")
    p.add_argument("--job", type=int)
    p.add_argument("--out")
    sub.add_parser("rescore")
    p = sub.add_parser("reassess")
    p.add_argument("--job", type=int, required=True)
    p.add_argument("--recrawl", action="store_true", help="re-extract evidence (uses the page cache when fresh)")
    p = sub.add_parser("ui")
    p.add_argument("--port", type=int, default=8530)
    p.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = ap.parse_args(argv)
    cfg = load_config()
    setup_logging(cfg.paths.log_dir, console=args.cmd in ("search", "resume"), level=logging.INFO)
    return {"init": cmd_init, "providers": cmd_providers, "parse": cmd_parse, "search": cmd_search,
            "resume": cmd_resume, "jobs": cmd_jobs, "top": cmd_top, "export": cmd_export, "rescore": cmd_rescore,
            "reassess": cmd_reassess,
            "ui": cmd_ui}[args.cmd](cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
