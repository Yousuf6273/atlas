"""Serve the SYNTHETIC fixture websites through respx (mocked HTTP; no network).

Maps files under tests/fixtures/synthetic/sites/<host>/ to URLs:
index.html → /, about.html → /about, careers/x.html → /careers/x,
robots.txt and sitemap.xml as-is. Any other path on these hosts → 404.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

SITES = Path(__file__).parent / "fixtures" / "synthetic" / "sites"


def _routes() -> dict[str, dict[str, Path]]:
    out: dict[str, dict[str, Path]] = {}
    for site in SITES.iterdir():
        if not site.is_dir():
            continue
        m: dict[str, Path] = {}
        for f in site.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(site).as_posix()
            if rel == "index.html":
                path = "/"
            elif rel.endswith(".html"):
                path = "/" + rel[:-5]
            else:
                path = "/" + rel
            m[path] = f
        out[site.name] = m
    return out


ROUTES = _routes()


def handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host.removeprefix("www.")
    path = request.url.path.rstrip("/") or "/"
    files = ROUTES.get(host, {})
    f = files.get(path)
    if f is None:
        return httpx.Response(404, text="not found", headers={"content-type": "text/html"})
    ctype = "text/plain" if f.suffix == ".txt" else ("application/xml" if f.suffix == ".xml" else "text/html; charset=utf-8")
    return httpx.Response(200, content=f.read_bytes(), headers={"content-type": ctype})


def mount(router: respx.Router) -> None:
    for host in ROUTES:
        router.route(host=host).mock(side_effect=handler)
        router.route(host="www." + host).mock(side_effect=handler)
