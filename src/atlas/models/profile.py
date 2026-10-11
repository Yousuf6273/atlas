"""Editable student profile used for task matching and outreach drafts.

Outreach drafts only ever state what is written here. Leave a field blank
rather than guessing; the generator omits blank fields.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SkillLevel = Literal["unspecified", "learning", "working knowledge", "confident"]


class SkillEntry(BaseModel):
    name: str
    level: SkillLevel = "unspecified"
    # Something concrete you have actually done with this skill. Used verbatim
    # in detailed emails when present.
    evidence: str = ""


class ProjectEntry(BaseModel):
    title: str
    description: str
    url: str | None = None


class StudentProfile(BaseModel):
    full_name: str = ""
    age: int | None = 17
    school: str = ""
    education_level: str = "A-level student"
    subjects: list[str] = Field(default_factory=lambda: ["Mathematics", "Physics", "Economics"])
    location_city: str = "Dubai"
    location_country: str = "United Arab Emirates"
    summer_year: int = 2027
    availability: str = "Summer 2027 (exact dates to be confirmed)"
    preferred_placement: str = "a short summer placement (2–4 weeks) or a small supervised project"
    interests: list[str] = Field(
        default_factory=lambda: [
            "aerospace",
            "mechanical engineering",
            "robotics",
            "AI",
            "software",
            "quantitative technology",
        ]
    )
    # The brief's list of contributions; levels are left unspecified until the
    # student sets them, so drafts never overstate ability.
    skills: list[SkillEntry] = Field(
        default_factory=lambda: [
            SkillEntry(name="Python"),
            SkillEntry(name="Excel"),
            SkillEntry(name="Research"),
            SkillEntry(name="Documentation"),
            SkillEntry(name="Basic CAD"),
            SkillEntry(name="Data analysis"),
            SkillEntry(name="Testing"),
        ]
    )
    projects: list[ProjectEntry] = Field(default_factory=list)
    email: str = ""
    phone: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""
    cv_summary: str = ""
    cv_attached_note: bool = True

    def skill_names(self) -> list[str]:
        return [s.name for s in self.skills]

    def normalized_skills(self) -> set[str]:
        return {s.name.strip().lower() for s in self.skills}

    def is_complete_for_outreach(self) -> list[str]:
        missing = []
        if not self.full_name.strip():
            missing.append("full name")
        if not self.email.strip():
            missing.append("email address")
        if not self.school.strip():
            missing.append("school")
        return missing
