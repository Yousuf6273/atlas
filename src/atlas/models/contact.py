"""Contact channels and verification results."""

from __future__ import annotations

from pydantic import BaseModel, Field

from atlas.models.enums import ContactChannel, EmailStatus, RoleCategory


class ContactCandidate(BaseModel):
    channel: ContactChannel
    value: str  # email address, form URL, phone (E.164), page URL
    name: str | None = None  # only if publicly published next to the contact
    role: str | None = None
    role_category: RoleCategory = RoleCategory.GENERAL
    is_generic: bool = True  # role mailbox (info@, careers@) vs individual
    source_type: str = "official_site"  # official_site | osm | wikidata | user | jsonld
    source_url: str | None = None
    snippet: str | None = None
    retrieved_at: str | None = None


class CheckResult(BaseModel):
    check: str  # syntax | domain | mx | source | consistency | individual
    passed: bool | None  # None = could not determine
    detail: str = ""


class EmailVerification(BaseModel):
    email: str
    status: EmailStatus
    checks: list[CheckResult] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
