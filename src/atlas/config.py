"""Application configuration.

Precedence (lowest → highest):
1. Defaults defined in the Pydantic models below.
2. ``config/atlas.toml`` (or the file named by ``ATLAS_CONFIG``).
3. Values saved from the Settings page (``app_settings`` table), applied via
   :func:`atlas.db.settings.effective_config`.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import httpx
from pydantic import BaseModel, Field, field_validator

from atlas import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PathSettings(BaseModel):
    data_dir: Path = Field(default_factory=lambda: Path(os.environ.get("ATLAS_DATA_DIR", PROJECT_ROOT / "data")))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "atlas.db"

    @property
    def cache_db_path(self) -> Path:
        return self.data_dir / "http_cache.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"


class HttpSettings(BaseModel):
    # Wikimedia and OSM ask automated clients to identify themselves with a
    # contact URL. Change this to your own page or repository.
    user_agent_name: str = "ATLAS-internship-research"
    contact_url: str = "https://github.com/Yousuf6273"
    timeout_s: float = 20.0
    connect_timeout_s: float = 10.0
    max_concurrency: int = Field(8, ge=1, le=32)
    per_domain_min_interval_s: float = Field(1.5, ge=0.5)
    max_retries: int = Field(3, ge=0, le=6)
    backoff_base_s: float = 1.0
    backoff_max_s: float = 30.0
    max_page_bytes: int = 3_000_000
    cache_ttl_hours: float = 72.0

    @property
    def user_agent(self) -> str:
        return (
            f"{self.user_agent_name}/{__version__} (+{self.contact_url}; personal internship research; "
            f"respects robots.txt) python-httpx/{httpx.__version__}"
        )


class CrawlSettings(BaseModel):
    max_pages_per_site: int = Field(10, ge=1, le=40)
    max_depth: int = Field(2, ge=0, le=4)
    # Upper bound on how many candidates one search job researches in depth.
    max_companies_to_research: int = Field(150, ge=1, le=1000)
    # Research up to target_count * multiplier candidates, because many
    # candidates are rejected after research.
    research_multiplier: float = Field(2.5, ge=1.0, le=10.0)
    company_concurrency: int = Field(6, ge=1, le=16)
    use_sitemaps: bool = True
    # Playwright is optional and only used when installed *and* enabled.
    enable_playwright: bool = False
    page_refresh_days: int = 14


class DiscoverySettings(BaseModel):
    overpass_endpoints: list[str] = Field(
        default_factory=lambda: [
            "https://overpass-api.de/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
        ]
    )
    overpass_tile_deg: float = Field(0.12, gt=0.01, le=1.0)
    overpass_query_timeout_s: int = Field(25, ge=10, le=180)
    overpass_max_tiles: int = Field(60, ge=1, le=400)
    wikidata_endpoint: str = "https://query.wikidata.org/sparql"
    gleif_endpoint: str = "https://api.gleif.org/api/v1"
    # Self-hosted SearXNG instance (disabled unless set). Must have the JSON
    # output format enabled. Public instances are NOT used.
    searxng_url: str | None = None
    # Try to find official websites for candidates that have none, using
    # name-derived domains that must pass identity verification.
    website_resolution: bool = True
    website_resolution_max: int = Field(40, ge=0, le=500)


class LLMSettings(BaseModel):
    enabled: bool = True
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b-instruct"
    timeout_s: float = 120.0
    max_retries: int = Field(2, ge=0, le=5)
    temperature: float = 0.0
    use_for_query_parsing: bool = True
    use_for_company_summary: bool = True
    use_for_draft_polish: bool = False


class ScoringWeights(BaseModel):
    industry: float = Field(25.0, ge=0)
    tasks: float = Field(25.0, ge=0)
    internship: float = Field(20.0, ge=0)
    contact: float = Field(15.0, ge=0)
    geography: float = Field(10.0, ge=0)
    activity: float = Field(5.0, ge=0)

    def normalized(self) -> dict[str, float]:
        raw = self.model_dump()
        total = sum(raw.values())
        if total <= 0:
            return {k: 1.0 / len(raw) for k in raw}
        return {k: v / total for k, v in raw.items()}


class VerificationSettings(BaseModel):
    dns_timeout_s: float = 5.0
    check_mx: bool = True


class QualificationSettings(BaseModel):
    min_relevance_qualified: float = Field(50.0, ge=0, le=100)
    min_relevance_review: float = Field(20.0, ge=0, le=100)


class AppConfig(BaseModel):
    paths: PathSettings = Field(default_factory=PathSettings)
    http: HttpSettings = Field(default_factory=HttpSettings)
    crawl: CrawlSettings = Field(default_factory=CrawlSettings)
    discovery: DiscoverySettings = Field(default_factory=DiscoverySettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    scoring: ScoringWeights = Field(default_factory=ScoringWeights)
    verification: VerificationSettings = Field(default_factory=VerificationSettings)
    qualification: QualificationSettings = Field(default_factory=QualificationSettings)

    @field_validator("paths", mode="before")
    @classmethod
    def _coerce_paths(cls, v):  # allow plain strings in TOML
        if isinstance(v, dict) and "data_dir" in v:
            v = {**v, "data_dir": Path(v["data_dir"]).expanduser()}
        return v

    def ensure_dirs(self) -> None:
        for p in (self.paths.data_dir, self.paths.log_dir, self.paths.export_dir):
            p.mkdir(parents=True, exist_ok=True)


def config_file_path() -> Path:
    return Path(os.environ.get("ATLAS_CONFIG", PROJECT_ROOT / "config" / "atlas.toml"))


def load_config(path: Path | None = None) -> AppConfig:
    """Load defaults overlaid with the TOML config file, if one exists."""
    path = path or config_file_path()
    data: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    cfg = AppConfig.model_validate(data)
    cfg.ensure_dirs()
    return cfg
