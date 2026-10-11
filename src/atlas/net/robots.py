"""robots.txt handling following RFC 9309.

* 2xx: parse and obey.
* 4xx (incl. 401/403): the file is "unavailable" — crawling is permitted.
* 5xx / network failure: "unreachable" — assume complete disallow.
"""

from __future__ import annotations

from dataclasses import dataclass

from protego import Protego


@dataclass
class RobotsPolicy:
    origin: str
    state: str  # parsed | unavailable | unreachable
    parser: Protego | None = None
    detail: str = ""

    def allowed(self, url: str, user_agent: str) -> bool:
        if self.state == "unavailable":
            return True
        if self.state == "unreachable" or self.parser is None:
            return False
        return bool(self.parser.can_fetch(url, user_agent))

    def crawl_delay(self, user_agent: str) -> float | None:
        if self.parser is None:
            return None
        d = self.parser.crawl_delay(user_agent)
        return float(d) if d is not None else None

    def sitemaps(self) -> list[str]:
        if self.parser is None:
            return []
        return list(self.parser.sitemaps)


def policy_from_response(origin: str, status: int | None, content: bytes | None, error: str | None) -> RobotsPolicy:
    if error is not None or status is None:
        return RobotsPolicy(origin, "unreachable", detail=error or "no response")
    if 200 <= status < 300:
        text = (content or b"").decode("utf-8", errors="replace")
        # Some servers return an HTML page with 200 for /robots.txt; treat that
        # as an empty (permissive) robots file, as major crawlers do.
        if "<html" in text[:500].lower():
            return RobotsPolicy(origin, "unavailable", detail="robots.txt returned HTML")
        try:
            return RobotsPolicy(origin, "parsed", Protego.parse(text))
        except Exception as exc:  # pragma: no cover - protego is permissive
            return RobotsPolicy(origin, "unreachable", detail=f"parse error: {exc}")
    if 400 <= status < 500:
        return RobotsPolicy(origin, "unavailable", detail=f"HTTP {status}")
    return RobotsPolicy(origin, "unreachable", detail=f"HTTP {status}")
