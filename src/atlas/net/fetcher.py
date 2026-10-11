"""Polite asynchronous HTTP client.

Features: robots.txt (RFC 9309), per-host rate limiting (incl. Crawl-delay),
global concurrency bound, retries with exponential backoff + jitter honouring
Retry-After, manual redirect handling (robots re-checked per hop), response
size caps, content-type validation, in-flight request de-duplication and a
persistent response cache.
"""

from __future__ import annotations

import asyncio
import email.utils
import random
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from atlas.config import HttpSettings
from atlas.logging_setup import get_logger, log_event
from atlas.net.cache import HttpCache
from atlas.net.ratelimit import HostRateLimiter
from atlas.net.robots import RobotsPolicy, policy_from_response

log = get_logger("net.fetcher")

HTML_TYPES = ("text/html", "application/xhtml+xml")
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_REDIRECTS = 5
_CHARSET_META = re.compile(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_\-]+)", re.IGNORECASE)


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    from_cache: bool = False
    error: str | None = None
    robots_blocked: bool = False
    truncated: bool = False
    redirects: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300

    @property
    def content_type(self) -> str | None:
        ct = self.headers.get("content-type")
        return ct.split(";")[0].strip().lower() if ct else None

    def text(self) -> str:
        charset = None
        ct = self.headers.get("content-type", "")
        m = re.search(r"charset=([\w\-]+)", ct, re.IGNORECASE)
        if m:
            charset = m.group(1)
        else:
            mm = _CHARSET_META.search(self.content[:4096])
            if mm:
                charset = mm.group(1).decode("ascii", "ignore")
        for enc in (charset, "utf-8", "cp1252"):
            if not enc:
                continue
            try:
                return self.content.decode(enc)
            except (LookupError, UnicodeDecodeError):
                continue
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        import json

        return json.loads(self.content.decode("utf-8", errors="replace"))


def tls_context():
    """System trust store (via truststore) when available.

    Many small-company sites omit intermediate certificates; browsers and the OS trust store recover the
    chain, OpenSSL's bundled store does not. Certificate verification stays fully enabled either way.
    """
    try:
        import ssl

        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # pragma: no cover - optional dependency
        return True


class Fetcher:
    """Use as ``async with Fetcher(settings, cache) as f: await f.get(url)``."""

    def __init__(self, settings: HttpSettings, cache: HttpCache | None = None, *,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.cache = cache
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._sem = asyncio.Semaphore(settings.max_concurrency)
        self._limiter = HostRateLimiter(settings.per_domain_min_interval_s)
        self._robots: dict[str, RobotsPolicy] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self.stats = {"requests": 0, "cache_hits": 0, "retries": 0, "robots_blocked": 0, "errors": 0}

    async def __aenter__(self) -> Fetcher:
        self._client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(self.settings.timeout_s, connect=self.settings.connect_timeout_s),
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept-Language": "en;q=1.0, ar;q=0.3",
                "Accept-Encoding": "gzip, deflate",
            },
            limits=httpx.Limits(max_connections=self.settings.max_concurrency * 2, max_keepalive_connections=10),
            transport=self._transport,
            verify=tls_context(),
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def user_agent(self) -> str:
        return self.settings.user_agent

    @property
    def robots_token(self) -> str:
        return self.settings.user_agent_name

    # ------------------------------------------------------------ robots

    async def robots_policy(self, url: str) -> RobotsPolicy:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}".lower()
        if origin in self._robots:
            return self._robots[origin]
        lock = self._robots_locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin in self._robots:
                return self._robots[origin]
            res = await self._request("GET", origin + "/robots.txt", check_robots=False, accept=None,
                                      max_bytes=500_000, cache_ttl_s=24 * 3600, follow_redirects=True)
            error = res.error if res.status is None else None
            policy = policy_from_response(origin, res.status, res.content, error)
            delay = policy.crawl_delay(self.robots_token)
            if delay:
                self._limiter.set_interval(parts.netloc.lower(), min(delay, 60.0))
            self._robots[origin] = policy
            return policy

    async def allowed(self, url: str) -> bool:
        policy = await self.robots_policy(url)
        return policy.allowed(url, self.robots_token)

    def crawl_delay(self, url: str) -> float:
        return self._limiter.interval(urlsplit(url).netloc.lower())

    # ------------------------------------------------------------ public API

    async def get(self, url: str, *, params: dict | None = None, headers: dict | None = None,
                  check_robots: bool = True, accept: Iterable[str] | None = HTML_TYPES,
                  max_bytes: int | None = None, cache_ttl_s: float | None = None,
                  use_cache: bool = True, retries: int | None = None) -> FetchResult:
        if params:
            url = str(httpx.URL(url, params=params))
        ttl = self.settings.cache_ttl_hours * 3600 if cache_ttl_s is None else cache_ttl_s
        return await self._dedup("GET", url, None, lambda: self._request(
            "GET", url, headers=headers, check_robots=check_robots, accept=accept, max_bytes=max_bytes,
            cache_ttl_s=ttl if use_cache else 0, follow_redirects=True, retries=retries))

    async def post_form(self, url: str, data: dict[str, str], *, headers: dict | None = None,
                        accept: Iterable[str] | None = None, cache_ttl_s: float = 0,
                        max_bytes: int | None = 50_000_000, retries: int | None = None) -> FetchResult:
        """POST to a documented API endpoint (robots.txt does not govern API calls)."""
        body = httpx.QueryParams(data).__str__().encode()
        return await self._dedup("POST", url, body, lambda: self._request(
            "POST", url, body=body, headers={**(headers or {}), "Content-Type": "application/x-www-form-urlencoded"},
            check_robots=False, accept=accept, max_bytes=max_bytes, cache_ttl_s=cache_ttl_s, follow_redirects=False,
            retries=retries))

    async def _dedup(self, method: str, url: str, body: bytes | None, factory) -> FetchResult:
        key = HttpCache.make_key(method, url, body)
        existing = self._inflight.get(key)
        if existing is not None:
            return await asyncio.shield(existing)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            result = await factory()
            fut.set_result(result)
            return result
        except BaseException as exc:
            fut.set_exception(exc)
            fut.exception()  # mark retrieved
            raise
        finally:
            self._inflight.pop(key, None)

    # ------------------------------------------------------------ core

    async def _request(self, method: str, url: str, *, body: bytes | None = None, headers: dict | None = None,
                       check_robots: bool, accept: Iterable[str] | None, max_bytes: int | None,
                       cache_ttl_s: float, follow_redirects: bool, retries: int | None = None) -> FetchResult:
        if self._client is None:
            raise RuntimeError("Fetcher must be used as an async context manager")
        start = time.monotonic()
        max_bytes = max_bytes or self.settings.max_page_bytes
        result = FetchResult(url=url, final_url=url)
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            if check_robots and not await self.allowed(current):
                self.stats["robots_blocked"] += 1
                result.robots_blocked = True
                result.error = "blocked by robots.txt"
                result.final_url = current
                log_event(log, "robots_blocked", url=current)
                break
            hop = await self._single(method, current, body=body, headers=headers, max_bytes=max_bytes,
                                     cache_ttl_s=cache_ttl_s, retries=retries)
            result.status, result.headers, result.content = hop.status, hop.headers, hop.content
            result.from_cache, result.error, result.truncated = hop.from_cache, hop.error, hop.truncated
            result.attempts += hop.attempts
            result.final_url = current
            location = hop.headers.get("location")
            if follow_redirects and hop.status in (301, 302, 303, 307, 308) and location:
                nxt = urljoin(current, location)
                if not nxt.lower().startswith(("http://", "https://")):
                    result.error = f"unsupported redirect target {nxt[:100]}"
                    break
                result.redirects.append(nxt)
                current = nxt
                if hop.status == 303:
                    method, body = "GET", None
                continue
            break
        else:
            result.error = "too many redirects"
        if result.error is None and result.status is not None and 300 <= result.status < 400:
            result.error = f"unfollowed redirect HTTP {result.status}"
        if accept and result.ok:
            ct = result.content_type or ""
            if ct and not any(ct.startswith(a) for a in accept):
                result.error = f"unexpected content-type {ct}"
                result.content = b""
        if result.error and not result.robots_blocked:
            self.stats["errors"] += 1
        result.elapsed_s = time.monotonic() - start
        return result

    async def _single(self, method: str, url: str, *, body: bytes | None, headers: dict | None, max_bytes: int,
                      cache_ttl_s: float, retries: int | None = None) -> FetchResult:
        max_retries = self.settings.max_retries if retries is None else retries
        key = HttpCache.make_key(method, url, body)
        if self.cache is not None and cache_ttl_s > 0:
            cached = self.cache.get(key)
            if cached is not None:
                self.stats["cache_hits"] += 1
                return FetchResult(url=url, final_url=cached.final_url, status=cached.status, headers=cached.headers,
                                   content=cached.content, from_cache=True)
        host = urlsplit(url).netloc.lower()
        res = FetchResult(url=url, final_url=url)
        for attempt in range(max_retries + 1):
            res.attempts = attempt + 1
            await self._limiter.wait(host)
            retry_after: float | None = None
            try:
                async with self._sem:
                    self.stats["requests"] += 1
                    req = self._client.build_request(method, url, content=body, headers=headers)
                    resp = await self._client.send(req, stream=True)
                    try:
                        chunks, total, truncated = [], 0, False
                        async for chunk in resp.aiter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                chunks.append(chunk[: max(0, max_bytes - (total - len(chunk)))])
                                truncated = True
                                break
                            chunks.append(chunk)
                    finally:
                        await resp.aclose()
                res.status = resp.status_code
                res.headers = {k.lower(): v for k, v in resp.headers.items()}
                res.content = b"".join(chunks)
                res.truncated = truncated
                res.error = None
                if resp.status_code in RETRY_STATUSES:
                    retry_after = _parse_retry_after(resp.headers.get("retry-after"))
                    res.error = f"HTTP {resp.status_code}"
                else:
                    break
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                res.status = None
                res.error = f"{type(exc).__name__}: {exc}"[:300]
            except httpx.HTTPError as exc:  # non-retryable protocol problems
                res.status = None
                res.error = f"{type(exc).__name__}: {exc}"[:300]
                break
            if attempt < max_retries:
                self.stats["retries"] += 1
                backoff = min(self.settings.backoff_max_s, self.settings.backoff_base_s * (2 ** attempt))
                delay = backoff * random.uniform(0.5, 1.5)
                if retry_after is not None:
                    delay = min(max(delay, retry_after), self.settings.backoff_max_s * 4)
                log_event(log, "http_retry", url=url, attempt=attempt + 1, error=res.error, delay_s=round(delay, 2))
                await asyncio.sleep(delay)
        if self.cache is not None and cache_ttl_s > 0 and res.status is not None and res.error is None:
            self.cache.put(key, url=url, final_url=url, status=res.status, headers=res.headers, content=res.content,
                           ttl_s=cache_ttl_s)
        return res


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, dt.timestamp() - time.time())
