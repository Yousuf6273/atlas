"""Discovery provider interface.

A provider turns :class:`SearchCriteria` into raw :class:`CandidateCompany`
records. Providers never write to the database; the pipeline handles
persistence, deduplication and provenance.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar

from atlas.config import AppConfig
from atlas.models.company import CandidateCompany
from atlas.models.enums import ProviderAvailability
from atlas.models.search import SearchCriteria
from atlas.net.fetcher import Fetcher


class ProviderError(Exception):
    """Raised when a provider cannot complete; the pipeline records it and continues."""


class ProviderDisabled(ProviderError):
    pass


@dataclass
class ProviderContext:
    cfg: AppConfig
    fetcher: Fetcher
    log: logging.Logger
    is_cancelled: Callable[[], bool] = lambda: False
    progress: Callable[[str], None] = lambda _msg: None


@dataclass
class ProviderResult:
    candidates: list[CandidateCompany] = field(default_factory=list)
    scanned: int = 0  # raw records inspected before relevance filtering
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    partial: bool = False  # some sub-queries failed; coverage incomplete


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    display_name: str
    availability: ProviderAvailability
    description: str
    coverage: str
    source_url: str
    limitations: str
    enabled_by_default: bool
    runs_in_search: bool  # False for manual/import-only providers


class DiscoveryProvider(ABC):
    info: ClassVar[ProviderInfo]

    @property
    def name(self) -> str:
        return self.info.name

    def applicable(self, criteria: SearchCriteria) -> tuple[bool, str]:
        """Whether this provider can serve the request (e.g. geography supported)."""
        return True, ""

    @abstractmethod
    async def discover(self, criteria: SearchCriteria, ctx: ProviderContext) -> ProviderResult: ...

    async def health_check(self, ctx: ProviderContext) -> tuple[bool, str]:
        return True, "no health check implemented"
