# Database schema

SQLite, WAL mode, foreign keys on. Migrations live in `src/atlas/db/migrations/NNNN_name.sql` and are applied
in order by `atlas.db.core.migrate` (tracked in `schema_migrations`). All timestamps are ISO-8601 UTC strings.

## Tables

| Table | Purpose | Notable constraints |
|---|---|---|
| `app_settings` | Settings-page overrides (`config.<section>`), student profile (`profile`), default area. | PK `key` |
| `discovery_providers` | Every source (implemented or deliberately not automated): availability, coverage, limitations, enabled flag, last run/success/error, result counts, last health check. | PK `name` |
| `search_jobs` | One row per search/import: query, criteria JSON, parser method, status, stage, progress, heartbeat, cancel flag, stats JSON. | `status` CHECK |
| `search_filters` | Normalised criteria (one row per filter value) for querying jobs. | UNIQUE(job, key, value) |
| `job_events` | Human-readable progress log per job. | |
| `provider_runs` | Result of each provider within a job (status, result count, errors, coverage notes). Used to resume. | UNIQUE(job, provider) |
| `companies` | Canonical company records: name, normalised name, website/domain (+ source and confidence), primary industry, business type, size band + evidence, qualification/internship summary, identity status, `merged_into_id`. | indexes on domain, normalised name, status |
| `job_companies` | Which companies belong to which job, pre-filter score, whether selected for research. | PK(job, company) |
| `company_locations` | Addresses/coordinates with provider provenance and location type. | unique per (company, address, coords, provider) |
| `company_sources` | Provenance: every provider record that contributed to a company (raw JSON kept). | UNIQUE(provider, external_id) |
| `field_history` | Every change (or rejected lower-confidence change) to versioned company fields, and merges. | |
| `crawl_jobs` | Per company × job crawl status, pages fetched, budget, attempts, errors. Used to resume. | UNIQUE(company, job) |
| `webpages` | Fetched pages: URL, final/canonical URL, HTTP status, content type, page type, title, content hash, cleaned text, word count, duplicate flag, error, fetch time. | UNIQUE(company, url) |
| `extracted_claims` | Evidence-linked statements: type, value, quoted snippet, source URL, webpage id, retrieval time, extractor (`rule:*` / `llm:*`), confidence, `is_inference`, `is_current`. | UNIQUE(company, type, normalised value, source URL) |
| `qualification_assessments` | Versioned qualification results (full JSON incl. reasons/evidence/uncertainties). | `is_current` |
| `internship_assessments` | Versioned internship-suitability results. | `is_current` |
| `task_opportunities` | Versioned task ideas with basis (observed/inferred), confidence and skill match. | basis CHECK |
| `contacts` | Channels (email, contact form, careers page, phone, LinkedIn company page, named person) with role category, generic/individual flag, source type/URL, verification status, quality score. | UNIQUE(company, channel, normalised value) |
| `contact_evidence` | Where each contact was seen (URL + snippet + time). | UNIQUE(contact, url) |
| `verification_results` | Individual checks per contact (syntax, mx, consistency, source, individual). | |
| `prospect_scores` | Versioned scores: total, six factors, suitability, evidence confidence, outreach readiness + label, weights used, full breakdown JSON. | `is_current` |
| `outreach_drafts` | Generated drafts per kind, edited text, facts used, method, approval. Approved drafts are never overwritten. | |
| `outreach_history` | CRM events (status, channel, contact used, draft, message snapshot, dates, notes, outcome). | |
| `do_not_contact` | Opt-outs by company, domain or email. | CHECK at least one set |
| `review_queue` | Possible duplicates, borderline qualifications, identity mismatches; resolution. | unique open item per pair |
| `evaluation_reviews` | Manual evaluation answers per company (relevance, duplicate, classification, contact validity, claims checked/unsupported, internship and outreach usefulness). | UNIQUE(company) |
| `llm_cache` | Cached validated LLM outputs keyed by model + prompts + schema. | PK `key` |

## Provenance and history guarantees

- Claims always have a source URL and snippet; `rule:`/`llm:` extractor prefixes distinguish methods.
- Lower-confidence values never silently replace higher-confidence ones (`field_history.action = 'kept_existing'`).
- Re-research retires old claims (`is_current = 0`) instead of deleting them.
- Merges keep all `company_sources`, locations, pages, claims and contacts on the surviving record.

## Inspecting the data

```bash
sqlite3 data/atlas.db ".tables"
sqlite3 data/atlas.db "SELECT name, qualification_status, internship_category FROM companies LIMIT 20;"
```
