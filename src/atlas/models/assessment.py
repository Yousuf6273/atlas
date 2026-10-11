"""Results of qualification, internship, task and scoring engines."""

from __future__ import annotations

from pydantic import BaseModel, Field

from atlas.models.company import Evidence
from atlas.models.enums import (
    BusinessType,
    GeoStatus,
    InternshipCategory,
    Level,
    OperationalStatus,
    QualificationStatus,
    SizeBand,
    Supervision,
    TaskBasis,
)


class IndustryScore(BaseModel):
    industry: str
    score: float  # 0..100, rubric in assess/qualification.py
    evidence: list[Evidence] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)


class QualificationResult(BaseModel):
    status: QualificationStatus
    target_relevance: float
    primary_industry: str | None
    industry_scores: list[IndustryScore] = Field(default_factory=list)
    business_type: BusinessType = BusinessType.UNKNOWN
    business_type_evidence: list[Evidence] = Field(default_factory=list)
    geo_status: GeoStatus = GeoStatus.UNKNOWN
    geo_evidence: list[str] = Field(default_factory=list)
    size_band: SizeBand = SizeBand.UNKNOWN
    size_evidence: str | None = None
    operational_status: OperationalStatus = OperationalStatus.UNCERTAIN
    operational_indicators: list[str] = Field(default_factory=list)
    has_engineering_team_evidence: bool = False
    reasons: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    method: str = "rules-v1"


class InternshipAssessment(BaseModel):
    category: InternshipCategory
    has_programme: bool = False
    school_students_welcome: bool = False
    university_only: bool = False
    min_age: int | None = None
    restricted_environment: bool = False
    uae_team: bool | None = None
    direct_outreach_practical: bool = True
    reasons: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    safety_flags: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    method: str = "rules-v1"


class TaskOpportunity(BaseModel):
    template_key: str
    title: str
    description: str
    rationale: str
    basis: TaskBasis
    evidence: list[Evidence] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    difficulty: Level
    supervision: Supervision
    confidence: Level
    skill_match: float = Field(1.0, ge=0, le=1)
    outreach_angle: str
    safety_note: str | None = None


class FactorScore(BaseModel):
    key: str
    label: str
    score: float  # 0..100
    weight: float  # normalised 0..1
    explanation: str


class ScoreBreakdown(BaseModel):
    total: float
    factors: list[FactorScore]
    suitability: float
    evidence_confidence: float
    contact_quality: float
    outreach_readiness: float
    readiness_label: str
    notes: list[str] = Field(default_factory=list)
