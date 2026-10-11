"""Per-host politeness: minimum interval between requests to the same host."""

from __future__ import annotations

import asyncio
import time


class HostRateLimiter:
    def __init__(self, default_interval_s: float):
        self.default_interval_s = default_interval_s
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}
        self._interval: dict[str, float] = {}

    def set_interval(self, host: str, interval_s: float) -> None:
        self._interval[host] = max(self.default_interval_s, interval_s)

    def interval(self, host: str) -> float:
        return self._interval.get(host, self.default_interval_s)

    async def wait(self, host: str) -> None:
        """Block until a request to ``host`` is allowed, then reserve the slot."""
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last.get(host)
            if last is not None:
                delay = self.interval(host) - (time.monotonic() - last)
                if delay > 0:
                    await asyncio.sleep(delay)
            self._last[host] = time.monotonic()
