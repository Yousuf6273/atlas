"""Structured search criteria produced by the query engine."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from atlas.models.enums import BusinessType, Industry, RoleCategory, SizeBand


class GeoArea(BaseModel):
    """A resolved geographic target.

    ``kind`` determines how discovery providers query it:
    - ``country``: ISO 3166-1 code (``AE``)
    - ``emirate``/``city``: ISO 3166-2 code (e.g. ``AE-DU``) + bounding box
    - ``zone``: free zone / district, centre point + radius (approximate)
    """

    key: str
    label: str
    kind: Literal["country", "emirate", "city", "zone"]
    country_code: str = "AE"
    iso_code: str | None = None
    bbox: tuple[float, float, float, float] | None = None  # south, west, north, east
    center: tuple[float, float] | None = None  # lat, lon
    radius_m: float | None = None
    parent_key: str | None = None
    aliases: list[str] = Field(default_factory=list)
    approximate: bool = False


class SearchCriteria(BaseModel):
    raw_query: str = ""
    areas: list[GeoArea] = Field(default_factory=list)
    country_code: str = "AE"
    industries: list[Industry] = Field(default_factory=list)
    excluded_industries: list[Industry] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    excluded_business_types: list[BusinessType] = Field(
        default_factory=lambda: [
            BusinessType.RECRUITMENT_AGENCY,
            BusinessType.TRAINING_PROVIDER,
            BusinessType.RETAIL_CONSUMER,
        ]
    )
    size_preference: list[SizeBand] = Field(default_factory=list)
    internship_type: Literal["summer", "short_placement", "project", "any"] = "summer"
    skills: list[str] = Field(default_factory=list)
    task_preferences: list[str] = Field(default_factory=list)
    desired_count: int = Field(50, ge=1, le=1000)
    contact_roles: list[RoleCategory] = Field(default_factory=list)
    min_quality: float = Field(0.0, ge=0, le=100)
    require_engineering_team: bool = False
    # Discovery providers to run. Empty = all enabled providers.
    providers: list[str] = Field(default_factory=list)

    @field_validator("keywords", "excluded_keywords", "skills", "task_preferences")
    @classmethod
    def _dedupe_lower(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for item in v:
            s = item.strip().lower()
            if s and s not in out:
                out.append(s)
        return out

    def summary(self) -> str:
        parts = []
        if self.industries:
            parts.append(", ".join(i.value for i in self.industries))
        if self.areas:
            parts.append("in " + " / ".join(a.label for a in self.areas))
        if self.size_preference:
            parts.append("size: " + ", ".join(s.value for s in self.size_preference))
        parts.append(f"target {self.desired_count}")
        return " · ".join(parts)


class ParsedQuery(BaseModel):
    criteria: SearchCriteria
    method: Literal["deterministic", "deterministic+llm", "manual"] = "deterministic"
    notes: list[str] = Field(default_factory=list)
    unrecognised_terms: list[str] = Field(default_factory=list)
    ambiguity: float = Field(0.0, ge=0, le=1)
