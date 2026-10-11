"""Turn provider candidates into deduplicated company records with provenance."""

from __future__ import annotations

import sqlite3

from atlas.db import companies, contacts, review
from atlas.db.companies import SOURCE_CONFIDENCE
from atlas.extract.contacts import EMAIL_RE, classify_local_part
from atlas.models.company import CandidateCompany
from atlas.models.contact import ContactCandidate
from atlas.models.enums import ContactChannel, RoleCategory
from atlas.resolve.matcher import CandidateFeatures, decide
from atlas.resolve.normalize import (
    is_company_domain,
    normalize_name,
    normalize_phone,
    registrable_domain,
    website_root,
)

PROVIDER_SOURCE_TYPE = {"osm_overpass": "osm", "wikidata": "wikidata", "gleif": "gleif", "manual_csv": "user",
                        "url_list": "user", "directory_page": "directory", "searxng": "search"}


def _structured_contacts(conn: sqlite3.Connection, company_id: int, cand: CandidateCompany) -> None:
    st = PROVIDER_SOURCE_TYPE.get(cand.provider, cand.provider)
    if cand.email:
        m = EMAIL_RE.search(cand.email)
        if m:
            email = f"{m.group(1)}@{m.group(2)}".lower()
            cat, generic = classify_local_part(m.group(1))
            contacts.upsert_contact(conn, company_id, ContactCandidate(
                channel=ContactChannel.EMAIL, value=email, role_category=cat if generic else RoleCategory.OTHER,
                is_generic=generic, source_type=st, source_url=cand.source_url,
                snippet=f"{cand.provider} record lists email {email}", retrieved_at=cand.retrieved_at))
    phone = normalize_phone(cand.phone) if cand.phone else None
    if phone:
        contacts.upsert_contact(conn, company_id, ContactCandidate(
            channel=ContactChannel.PHONE, value=phone, source_type=st, source_url=cand.source_url,
            snippet=f"{cand.provider} record lists phone {cand.phone}", retrieved_at=cand.retrieved_at))


def ingest_candidate(conn: sqlite3.Connection, cand: CandidateCompany, job_id: int | None) -> tuple[int, str]:
    """Insert or match a candidate. Returns (company_id, action)."""
    website = website_root(cand.website) if cand.website else None
    dom = registrable_domain(website) if website else None
    if dom and not is_company_domain(dom):
        website, dom = None, None
    conf = SOURCE_CONFIDENCE.get(PROVIDER_SOURCE_TYPE.get(cand.provider, cand.provider),
                                 SOURCE_CONFIDENCE.get(cand.provider, 0.5))

    existing = companies.find_by_source(conn, cand.provider, cand.external_id)
    if existing is not None:
        action = "known_source"
        cid = existing
    else:
        feats = CandidateFeatures.of(cand.model_copy(update={"website": website}))
        tokens = [t for t in feats.tokens if len(t) >= 3]
        pool = companies.match_pool(conn, domain=feats.domain, phones=feats.phones, name_tokens=tokens)
        decision = decide(feats, pool)
        if decision.action == "merge" and decision.company_id is not None:
            cid = decision.company_id
            action = "merged"
        else:
            cid = companies.create_company(conn, name=cand.name, normalized_name=normalize_name(cand.name),
                                           website=website, domain=dom, website_source=cand.provider if website else None,
                                           website_confidence=conf if website else None, description=cand.description)
            action = "new"
            for other_id, score, reason, rel in decision.reviews:
                review.enqueue(conn, item_type="possible_duplicate", company_id=cid, other_company_id=other_id,
                               score=score, reason=reason, details={"relationship": rel, "provider": cand.provider})
                action = "new_needs_review"
    if website:
        company = companies.get_company(conn, cid)
        if company and company.get("website") is None:
            companies.set_field(conn, cid, "website", website, confidence=conf, source=cand.provider)
            companies.set_field(conn, cid, "domain", dom, confidence=conf, source=cand.provider)
            companies.set_identity(conn, cid, "unverified", None)
        elif company and company.get("domain") and dom and company["domain"] != dom:
            # A lower-confidence conflicting website is recorded in history but not applied.
            if companies.set_field(conn, cid, "website", website, confidence=min(conf, 0.5), source=cand.provider):
                companies.set_field(conn, cid, "domain", dom, confidence=min(conf, 0.5), source=cand.provider)
    if cand.description:
        companies.set_field(conn, cid, "description", cand.description[:1000], confidence=conf, source=cand.provider)
    companies.add_source(conn, cid, cand, job_id)
    if cand.location:
        companies.add_location(conn, cid, cand.location, provider=cand.provider, source_url=cand.source_url)
    _structured_contacts(conn, cid, cand)
    if job_id is not None:
        companies.link_job(conn, job_id, cid, cand.relevance_hint)
    return cid, action
