"""Optional local inference through Ollama (http://localhost:11434).

* Detects availability via ``GET /api/tags``; everything falls back to
  deterministic code when Ollama is absent.
* Structured outputs: the Pydantic schema is passed as Ollama's ``format``
  (JSON schema) and the reply is validated; malformed replies are retried with
  the validation error, up to ``max_retries``.
* Responses are cached in the ``llm_cache`` table keyed by model + prompts +
  schema, so repeated research does not repeat inference.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from atlas.config import LLMSettings
from atlas.logging_setup import get_logger, log_event
from atlas.timeutil import now_iso

log = get_logger("llm")
T = TypeVar("T", bound=BaseModel)


def has_model(wanted: str, installed: list[str]) -> bool:
    return wanted in installed or (":" not in wanted and f"{wanted}:latest" in installed)


class OllamaClient:
    def __init__(self, settings: LLMSettings, conn: sqlite3.Connection | None = None, *,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.conn = conn
        self._transport = transport
        self._available: bool | None = None
        self.models: list[str] = []
        self.stats = {"calls": 0, "cache_hits": 0, "invalid": 0, "failures": 0}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.settings.base_url, timeout=self.settings.timeout_s,
                                 transport=self._transport)

    async def check(self) -> tuple[bool, str]:
        """Is Ollama running and is the configured model installed?"""
        if not self.settings.enabled:
            self._available = False
            return False, "local AI disabled in settings"
        try:
            async with self._client() as c:
                r = await c.get("/api/tags", timeout=3.0)
            r.raise_for_status()
            self.models = [m.get("name", "") for m in r.json().get("models", [])]
        except (httpx.HTTPError, ValueError) as exc:
            self._available = False
            return False, f"Ollama not reachable at {self.settings.base_url} ({type(exc).__name__})"
        wanted = self.settings.model
        if not has_model(wanted, self.models):
            self._available = False
            return False, f"Ollama is running but model '{wanted}' is not installed (have: {', '.join(self.models) or 'none'})"
        self._available = True
        return True, f"Ollama available with model {wanted}"

    async def available(self) -> bool:
        if self._available is None:
            await self.check()
        return bool(self._available)

    def _cache_key(self, system: str, prompt: str, schema: type[BaseModel]) -> str:
        h = hashlib.sha256()
        for part in (self.settings.model, str(self.settings.temperature), system, prompt,
                     json.dumps(schema.model_json_schema(), sort_keys=True)):
            h.update(part.encode())
            h.update(b"\0")
        return h.hexdigest()

    def _cache_get(self, key: str) -> str | None:
        if self.conn is None:
            return None
        row = self.conn.execute("SELECT response_json FROM llm_cache WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _cache_put(self, key: str, value: str) -> None:
        if self.conn is not None:
            self.conn.execute("INSERT OR REPLACE INTO llm_cache(key, model, response_json, created_at) VALUES (?, ?, ?, ?)",
                              (key, self.settings.model, value, now_iso()))

    async def structured(self, *, system: str, prompt: str, schema: type[T], max_retries: int | None = None) -> T | None:
        """Return a validated ``schema`` instance, or None if unavailable/invalid."""
        if not await self.available():
            return None
        key = self._cache_key(system, prompt, schema)
        cached = self._cache_get(key)
        if cached is not None:
            try:
                self.stats["cache_hits"] += 1
                return schema.model_validate_json(cached)
            except ValidationError:
                pass
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        retries = self.settings.max_retries if max_retries is None else max_retries
        for attempt in range(retries + 1):
            self.stats["calls"] += 1
            try:
                async with self._client() as c:
                    r = await c.post("/api/chat", json={
                        "model": self.settings.model, "messages": messages, "stream": False,
                        "format": schema.model_json_schema(),
                        "options": {"temperature": self.settings.temperature},
                    })
                r.raise_for_status()
                content = r.json().get("message", {}).get("content", "")
            except (httpx.HTTPError, ValueError) as exc:
                self.stats["failures"] += 1
                log_event(log, "llm_call_failed", error=str(exc)[:200], attempt=attempt)
                return None
            try:
                obj = schema.model_validate_json(content)
            except ValidationError as exc:
                self.stats["invalid"] += 1
                log_event(log, "llm_invalid_output", attempt=attempt, error=str(exc)[:300])
                messages += [{"role": "assistant", "content": content[:4000]},
                             {"role": "user", "content": "That output did not match the JSON schema: "
                              f"{str(exc)[:500]}. Return only valid JSON matching the schema."}]
                continue
            self._cache_put(key, obj.model_dump_json())
            return obj
        return None
