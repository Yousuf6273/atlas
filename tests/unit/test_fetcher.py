"""HTTP layer: robots.txt, retries/backoff, redirects, size caps, content types, caching, de-duplication."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from atlas.net.cache import HttpCache
from atlas.net.fetcher import Fetcher

HTML = b"<html><head><title>T</title></head><body>hello</body></html>"


def _html(status=200, body=HTML, **headers):
    return httpx.Response(status, content=body, headers={"content-type": "text/html; charset=utf-8", **headers})


@pytest.fixture
def fetcher_factory(cfg):
    def make(cache=False):
        c = HttpCache(cfg.paths.cache_db_path) if cache else None
        return Fetcher(cfg.http, c)
    return make


@respx.mock
async def test_robots_disallow_blocks_fetch(fetcher_factory):
    respx.get("https://site.example/robots.txt").mock(return_value=httpx.Response(
        200, text="User-agent: *\nDisallow: /private\n"))
    page = respx.get("https://site.example/private/x").mock(return_value=_html())
    async with fetcher_factory() as f:
        res = await f.get("https://site.example/private/x")
    assert res.robots_blocked and not res.ok
    assert page.call_count == 0


@respx.mock
async def test_robots_5xx_means_disallow_and_404_means_allow(fetcher_factory):
    respx.get("https://down.example/robots.txt").mock(return_value=httpx.Response(503))
    respx.get("https://open.example/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://open.example/").mock(return_value=_html())
    respx.get("https://down.example/").mock(return_value=_html())
    async with fetcher_factory() as f:
        assert (await f.get("https://down.example/")).robots_blocked
        assert (await f.get("https://open.example/")).ok


@respx.mock
async def test_crawl_delay_respected(fetcher_factory):
    respx.get("https://slow.example/robots.txt").mock(return_value=httpx.Response(
        200, text="User-agent: *\nCrawl-delay: 7\n"))
    respx.get("https://slow.example/").mock(return_value=_html())
    async with fetcher_factory() as f:
        await f.get("https://slow.example/")
        assert f.crawl_delay("https://slow.example/") == 7


@respx.mock
async def test_retry_on_503_then_success(fetcher_factory):
    respx.get("https://flaky.example/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get("https://flaky.example/").mock(side_effect=[httpx.Response(503), _html()])
    async with fetcher_factory() as f:
        res = await f.get("https://flaky.example/")
    assert res.ok and route.call_count == 2 and res.attempts == 2


@respx.mock
async def test_gives_up_after_max_retries(fetcher_factory, cfg):
    respx.get("https://dead.example/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get("https://dead.example/").mock(side_effect=httpx.ConnectError("boom"))
    async with fetcher_factory() as f:
        res = await f.get("https://dead.example/")
    assert not res.ok and "ConnectError" in res.error
    assert route.call_count == cfg.http.max_retries + 1


@respx.mock
async def test_no_retry_on_404(fetcher_factory):
    respx.get("https://x.example/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get("https://x.example/missing").mock(return_value=_html(404))
    async with fetcher_factory() as f:
        res = await f.get("https://x.example/missing")
    assert res.status == 404 and not res.ok and route.call_count == 1


@respx.mock
async def test_redirect_followed_and_robots_checked_per_host(fetcher_factory):
    respx.get("https://a.example/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://b.example/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n"))
    respx.get("https://a.example/").mock(return_value=httpx.Response(301, headers={"location": "https://b.example/"}))
    target = respx.get("https://b.example/").mock(return_value=_html())
    async with fetcher_factory() as f:
        res = await f.get("https://a.example/")
    assert res.robots_blocked and target.call_count == 0
    assert res.redirects == ["https://b.example/"]


@respx.mock
async def test_content_type_validation_and_size_cap(fetcher_factory, cfg):
    respx.get("https://c.example/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://c.example/file.pdf").mock(return_value=httpx.Response(
        200, content=b"%PDF", headers={"content-type": "application/pdf"}))
    respx.get("https://c.example/big").mock(return_value=_html(body=b"x" * 5000))
    async with fetcher_factory() as f:
        pdf = await f.get("https://c.example/file.pdf")
        big = await f.get("https://c.example/big", max_bytes=1000)
    assert "content-type" in pdf.error and pdf.content == b""
    assert big.truncated and len(big.content) == 1000


@respx.mock
async def test_cache_hit_avoids_second_request(fetcher_factory):
    respx.get("https://cache.example/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get("https://cache.example/").mock(return_value=_html())
    async with fetcher_factory(cache=True) as f:
        r1 = await f.get("https://cache.example/")
    async with fetcher_factory(cache=True) as f:
        r2 = await f.get("https://cache.example/")
    assert r1.ok and r2.ok and r2.from_cache and route.call_count == 1


@respx.mock
async def test_concurrent_duplicate_requests_deduplicated(fetcher_factory):
    respx.get("https://dup.example/robots.txt").mock(return_value=httpx.Response(404))

    async def slow(_req):
        await asyncio.sleep(0.05)
        return _html()

    route = respx.get("https://dup.example/").mock(side_effect=slow)
    async with fetcher_factory() as f:
        results = await asyncio.gather(*[f.get("https://dup.example/") for _ in range(5)])
    assert all(r.ok for r in results) and route.call_count == 1


@respx.mock
async def test_retry_after_header_honoured(fetcher_factory, cfg):
    cfg.http.backoff_max_s = 0.5
    respx.get("https://ra.example/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ra.example/").mock(side_effect=[httpx.Response(429, headers={"retry-after": "0"}), _html()])
    async with fetcher_factory() as f:
        res = await f.get("https://ra.example/")
    assert res.ok and res.attempts == 2


def test_text_decoding_uses_meta_charset():
    from atlas.net.fetcher import FetchResult

    body = '<html><head><meta charset="cp1252"></head><body>café</body></html>'.encode("cp1252")
    r = FetchResult(url="u", final_url="u", status=200, headers={"content-type": "text/html"}, content=body)
    assert "café" in r.text()


@respx.mock
async def test_crawler_distinguishes_robots_disallow_from_unavailable_and_dead(cfg):
    from atlas.crawl.site_crawler import SiteCrawler

    respx.get("https://disallow.example/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n"))
    respx.get("https://err500.example/robots.txt").mock(return_value=httpx.Response(500))
    respx.get("https://dead.example/robots.txt").mock(side_effect=httpx.ConnectError("nodename nor servname provided"))
    async with Fetcher(cfg.http, None) as f:
        crawler = SiteCrawler(f, cfg.crawl)
        a = await crawler.crawl("https://disallow.example/")
        b = await crawler.crawl("https://err500.example/")
        c = await crawler.crawl("https://dead.example/")
    assert a.robots_blocked and not a.robots_unavailable
    assert b.robots_unavailable and not b.robots_blocked and "RFC 9309" in b.notes[0]
    assert not c.robots_blocked and not c.robots_unavailable and not c.reachable and "unreachable" in c.errors[0]
