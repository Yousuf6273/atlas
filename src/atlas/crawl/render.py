"""Optional JavaScript rendering via Playwright.

Used only when (a) ``crawl.enable_playwright`` is true, (b) the ``playwright``
package and a Chromium build are installed, and (c) a page appears to need
JavaScript. robots.txt has already been checked by the caller. Not installed
by default; see docs/PROVIDERS.md.
"""

from __future__ import annotations

import importlib.util

from atlas.logging_setup import get_logger, log_event

log = get_logger("crawl.render")


def playwright_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


class PlaywrightRenderer:
    def __init__(self, user_agent: str, timeout_ms: int = 20000):
        self.user_agent = user_agent
        self.timeout_ms = timeout_ms
        self._pw = None
        self._browser = None

    async def __aenter__(self) -> PlaywrightRenderer:
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def render(self, url: str) -> str | None:
        try:
            ctx = await self._browser.new_context(user_agent=self.user_agent, java_script_enabled=True)
            page = await ctx.new_page()
            await page.goto(url, timeout=self.timeout_ms, wait_until="networkidle")
            html = await page.content()
            await ctx.close()
            return html
        except Exception as exc:  # rendering is best-effort
            log_event(log, "render_failed", url=url, error=str(exc)[:200])
            return None
