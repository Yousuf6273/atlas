"""A company's research dossier assembled from the database.

Assessment engines read only from a dossier, so every judgement can be
re-computed from persisted evidence (e.g. after changing settings).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from atlas.db import companies, contacts, research
from atlas.db.core import loads


@dataclass
class PageDoc:
    id: int
    url: str
    page_type: str
    title: str
    text: str
    fetched_at: str
    status_code: int | None
    error: str | None
    is_duplicate: bool


@dataclass
class Dossier:
    company: dict[str, Any]
    sources: list[dict[str, Any]] = field(default_factory=list)
    locations: list[dict[str, Any]] = field(default_factory=list)
    pages: list[PageDoc] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    contacts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def id(self) -> int:
        return int(self.company["id"])

    @property
    def name(self) -> str:
        return self.company["name"]

    @property
    def good_pages(self) -> list[PageDoc]:
        return [p for p in self.pages if not p.error and not p.is_duplicate and p.text]

    def claims_of(self, *types: str) -> list[dict[str, Any]]:
        return [c for c in self.claims if c["claim_type"] in types]

    def page_type_of(self, url: str) -> str:
        for p in self.pages:
            if p.url == url:
                return p.page_type
        return "other"

    def site_status(self) -> str | None:
        st = self.claims_of("site_status")
        return st[0]["value"] if st else None

    def provider_tags(self, provider: str) -> list[dict[str, Any]]:
        out = []
        for s in self.sources:
            if s["provider"] == provider:
                raw = loads(s.get("raw_json"), {}) or {}
                out.append(raw)
        return out

    def providers(self) -> set[str]:
        return {s["provider"] for s in self.sources}


def load_dossier(conn: sqlite3.Connection, company_id: int) -> Dossier:
    company = companies.get_company(conn, company_id)
    if company is None:
        raise KeyError(f"company {company_id} not found")
    pages = [
        PageDoc(id=p["id"], url=p["final_url"] or p["url"], page_type=p["page_type"] or "other", title=p["title"] or "",
                text=p["text_content"] or "", fetched_at=p["fetched_at"], status_code=p["status_code"],
                error=p["error"], is_duplicate=bool(p["is_duplicate"]))
        for p in research.company_pages(conn, company_id)
    ]
    return Dossier(
        company=company,
        sources=companies.company_sources(conn, company_id),
        locations=companies.company_locations(conn, company_id),
        pages=pages,
        claims=research.company_claims(conn, company_id),
        contacts=contacts.company_contacts(conn, company_id),
    )
