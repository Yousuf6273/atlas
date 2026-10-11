"""Company candidates (from discovery providers) and evidence records."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from atlas.models.enums import LocationType


class CandidateLocation(BaseModel):
    address: str | None = None
    city: str | None = None
    emirate: str | None = None
    country_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_type: LocationType = LocationType.UNKNOWN


class CandidateCompany(BaseModel):
    """A raw company record as returned by a discovery provider.

    Nothing here is trusted yet: it is validated during research.
    """

    name: str
    provider: str
    external_id: str
    source_url: str | None = None
    website: str | None = None
    email: str | None = None
    phone: str | None = None
    description: str | None = None
    location: CandidateLocation | None = None
    industry_hints: list[str] = Field(default_factory=list)
    tags: dict[str, Any] = Field(default_factory=dict)
    employees: int | None = None  # only when the source states a figure
    employees_source: str | None = None
    retrieved_at: str | None = None
    relevance_hint: float = 0.0  # provider-side keyword relevance, 0..1


class Evidence(BaseModel):
    source_url: str
    snippet: str
    retrieved_at: str | None = None
    webpage_id: int | None = None


class Claim(BaseModel):
    claim_type: str  # e.g. activity, industry_signal, internship_signal, location, size
    value: str
    evidence: Evidence
    extractor: str  # rule:<name> or llm:<model>
    confidence: float = Field(0.5, ge=0, le=1)
    is_inference: bool = False
