"""Shared state for one pipeline run (one search job or one company refresh)."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field

from atlas.config import AppConfig
from atlas.contacts.verify import DomainMailChecker
from atlas.crawl.render import PlaywrightRenderer
from atlas.db import jobs
from atlas.llm.client import OllamaClient
from atlas.logging_setup import get_logger, log_event
from atlas.models.profile import StudentProfile
from atlas.net.fetcher import Fetcher


class JobCancelled(Exception):
    pass


@dataclass
class PipelineContext:
    cfg: AppConfig
    conn: sqlite3.Connection
    fetcher: Fetcher
    mail_checker: DomainMailChecker
    profile: StudentProfile
    job_id: int | None = None
    llm: OllamaClient | None = None
    renderer: PlaywrightRenderer | None = None
    log: logging.Logger = field(default_factory=lambda: get_logger("pipeline"))
    _cancel_checked_at: float = 0.0
    _cancelled: bool = False
    websites_resolved: int = 0
    counters: dict[str, int] = field(default_factory=dict)

    def cancelled(self) -> bool:
        if self.job_id is None:
            return False
        now = time.monotonic()
        if now - self._cancel_checked_at > 1.0:
            self._cancel_checked_at = now
            self._cancelled = jobs.cancel_requested(self.conn, self.job_id)
        return self._cancelled

    def check_cancel(self) -> None:
        if self.cancelled():
            raise JobCancelled()

    def progress(self, stage: str, done: int | None = None, total: int | None = None, message: str | None = None) -> None:
        if self.job_id is not None:
            jobs.update_progress(self.conn, self.job_id, stage=stage, done=done, total=total, message=message)

    def event(self, message: str, *, level: str = "info", stage: str | None = None, **data) -> None:
        if self.job_id is not None:
            jobs.add_event(self.conn, self.job_id, message, level=level, stage=stage, data=data or None)
        log_event(self.log, message, level=getattr(logging, level.upper(), logging.INFO), job=self.job_id,
                  stage=stage, **data)

    def bump(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + n
