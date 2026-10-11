-- ATLAS initial schema. All timestamps are ISO-8601 UTC strings.

CREATE TABLE app_settings (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- ---------------------------------------------------------------- discovery
CREATE TABLE discovery_providers (
    name              TEXT PRIMARY KEY,
    display_name      TEXT NOT NULL,
    enabled           INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    availability      TEXT NOT NULL,
    description       TEXT,
    coverage          TEXT,
    source_url        TEXT,
    limitations       TEXT,
    last_run_at       TEXT,
    last_success_at   TEXT,
    last_error        TEXT,
    last_error_at     TEXT,
    last_result_count INTEGER,
    total_runs        INTEGER NOT NULL DEFAULT 0,
    total_results     INTEGER NOT NULL DEFAULT 0,
    last_health       TEXT,
    last_health_at    TEXT,
    updated_at        TEXT NOT NULL
);

CREATE TABLE search_jobs (
    id               INTEGER PRIMARY KEY,
    query_text       TEXT NOT NULL,
    criteria_json    TEXT NOT NULL,
    parser_method    TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('pending','running','completed','failed','cancelled','interrupted')),
    stage            TEXT,
    progress_done    INTEGER NOT NULL DEFAULT 0,
    progress_total   INTEGER NOT NULL DEFAULT 0,
    message          TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    target_count     INTEGER,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    heartbeat_at     TEXT,
    error            TEXT,
    stats_json       TEXT
);
CREATE INDEX ix_search_jobs_status ON search_jobs(status);

CREATE TABLE search_filters (
    id           INTEGER PRIMARY KEY,
    job_id       INTEGER NOT NULL REFERENCES search_jobs(id) ON DELETE CASCADE,
    filter_key   TEXT NOT NULL,
    filter_value TEXT NOT NULL,
    UNIQUE (job_id, filter_key, filter_value)
);

CREATE TABLE job_events (
    id        INTEGER PRIMARY KEY,
    job_id    INTEGER NOT NULL REFERENCES search_jobs(id) ON DELETE CASCADE,
    ts        TEXT NOT NULL,
    level     TEXT NOT NULL,
    stage     TEXT,
    message   TEXT NOT NULL,
    data_json TEXT
);
CREATE INDEX ix_job_events_job ON job_events(job_id, id);

CREATE TABLE provider_runs (
    id           INTEGER PRIMARY KEY,
    job_id       INTEGER NOT NULL REFERENCES search_jobs(id) ON DELETE CASCADE,
    provider     TEXT NOT NULL REFERENCES discovery_providers(name),
    status       TEXT NOT NULL CHECK (status IN ('running','completed','failed','skipped')),
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    notes        TEXT,
    UNIQUE (job_id, provider)
);

-- ---------------------------------------------------------------- companies
CREATE TABLE companies (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT NOT NULL,
    normalized_name      TEXT NOT NULL,
    website              TEXT,
    domain               TEXT,
    website_source       TEXT,
    website_confidence   REAL,
    description          TEXT,
    primary_industry     TEXT,
    business_type        TEXT,
    size_band            TEXT NOT NULL DEFAULT 'unknown',
    size_evidence        TEXT,
    qualification_status TEXT CHECK (qualification_status IN ('qualified','rejected','needs_review')),
    internship_category  TEXT,
    identity_status      TEXT NOT NULL DEFAULT 'unverified'
                         CHECK (identity_status IN ('unverified','verified','mismatch','no_website')),
    identity_notes       TEXT,
    merged_into_id       INTEGER REFERENCES companies(id),
    first_seen_at        TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    last_researched_at   TEXT
);
CREATE INDEX ix_companies_domain ON companies(domain);
CREATE INDEX ix_companies_normname ON companies(normalized_name);
CREATE INDEX ix_companies_status ON companies(qualification_status);

CREATE TABLE job_companies (
    job_id         INTEGER NOT NULL REFERENCES search_jobs(id) ON DELETE CASCADE,
    company_id     INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    added_at       TEXT NOT NULL,
    prefilter_score REAL,
    selected_for_research INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, company_id)
);

CREATE TABLE company_locations (
    id              INTEGER PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    address         TEXT,
    city            TEXT,
    emirate         TEXT,
    country_code    TEXT,
    latitude        REAL,
    longitude       REAL,
    location_type   TEXT NOT NULL DEFAULT 'unknown',
    source_provider TEXT NOT NULL,
    source_url      TEXT,
    retrieved_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_company_locations ON company_locations(
    company_id, COALESCE(address, ''), COALESCE(round(latitude, 5), 0), COALESCE(round(longitude, 5), 0), source_provider
);

-- Provenance: every provider record that contributed to a company.
CREATE TABLE company_sources (
    id           INTEGER PRIMARY KEY,
    company_id   INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    provider     TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    raw_name     TEXT NOT NULL,
    source_url   TEXT,
    raw_json     TEXT,
    job_id       INTEGER REFERENCES search_jobs(id) ON DELETE SET NULL,
    retrieved_at TEXT NOT NULL,
    UNIQUE (provider, external_id)
);
CREATE INDEX ix_company_sources_company ON company_sources(company_id);

-- Field history: prior values are never silently lost.
CREATE TABLE field_history (
    id             INTEGER PRIMARY KEY,
    company_id     INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    field          TEXT NOT NULL,
    old_value      TEXT,
    new_value      TEXT,
    old_confidence REAL,
    new_confidence REAL,
    source         TEXT,
    action         TEXT NOT NULL CHECK (action IN ('overwritten','kept_existing','set')),
    changed_at     TEXT NOT NULL
);
CREATE INDEX ix_field_history_company ON field_history(company_id);

-- ---------------------------------------------------------------- research
CREATE TABLE crawl_jobs (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    job_id        INTEGER REFERENCES search_jobs(id) ON DELETE CASCADE,
    status        TEXT NOT NULL CHECK (status IN ('pending','running','done','failed','skipped')),
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    page_budget   INTEGER NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    started_at    TEXT,
    finished_at   TEXT,
    error         TEXT,
    notes         TEXT,
    UNIQUE (company_id, job_id)
);

CREATE TABLE webpages (
    id             INTEGER PRIMARY KEY,
    company_id     INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    url            TEXT NOT NULL,
    final_url      TEXT,
    canonical_url  TEXT,
    status_code    INTEGER,
    content_type   TEXT,
    page_type      TEXT,
    title          TEXT,
    content_hash   TEXT,
    text_content   TEXT,
    word_count     INTEGER,
    lang           TEXT,
    needs_js       INTEGER NOT NULL DEFAULT 0,
    is_duplicate   INTEGER NOT NULL DEFAULT 0,
    error          TEXT,
    fetched_at     TEXT NOT NULL,
    UNIQUE (company_id, url)
);
CREATE INDEX ix_webpages_company ON webpages(company_id);
CREATE INDEX ix_webpages_hash ON webpages(company_id, content_hash);

CREATE TABLE extracted_claims (
    id               INTEGER PRIMARY KEY,
    company_id       INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    claim_type       TEXT NOT NULL,
    value            TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    evidence_snippet TEXT NOT NULL,
    source_url       TEXT NOT NULL,
    webpage_id       INTEGER REFERENCES webpages(id) ON DELETE SET NULL,
    retrieved_at     TEXT NOT NULL,
    extractor        TEXT NOT NULL,
    confidence       REAL NOT NULL,
    is_inference     INTEGER NOT NULL DEFAULT 0,
    is_current       INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    UNIQUE (company_id, claim_type, normalized_value, source_url)
);
CREATE INDEX ix_claims_company ON extracted_claims(company_id, claim_type);

CREATE TABLE qualification_assessments (
    id                 INTEGER PRIMARY KEY,
    company_id         INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    job_id             INTEGER REFERENCES search_jobs(id) ON DELETE SET NULL,
    status             TEXT NOT NULL,
    target_relevance   REAL NOT NULL,
    primary_industry   TEXT,
    business_type      TEXT,
    geo_status         TEXT,
    size_band          TEXT,
    operational_status TEXT,
    result_json        TEXT NOT NULL,
    method             TEXT NOT NULL,
    is_current         INTEGER NOT NULL DEFAULT 1,
    assessed_at        TEXT NOT NULL
);
CREATE INDEX ix_qual_company ON qualification_assessments(company_id, is_current);

CREATE TABLE internship_assessments (
    id          INTEGER PRIMARY KEY,
    company_id  INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    category    TEXT NOT NULL,
    result_json TEXT NOT NULL,
    method      TEXT NOT NULL,
    is_current  INTEGER NOT NULL DEFAULT 1,
    assessed_at TEXT NOT NULL
);
CREATE INDEX ix_intern_company ON internship_assessments(company_id, is_current);

CREATE TABLE task_opportunities (
    id             INTEGER PRIMARY KEY,
    company_id     INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    template_key   TEXT NOT NULL,
    title          TEXT NOT NULL,
    basis          TEXT NOT NULL CHECK (basis IN ('observed_need','inferred_opportunity')),
    confidence     TEXT NOT NULL,
    skill_match    REAL NOT NULL,
    result_json    TEXT NOT NULL,
    is_current     INTEGER NOT NULL DEFAULT 1,
    assessed_at    TEXT NOT NULL
);
CREATE INDEX ix_tasks_company ON task_opportunities(company_id, is_current);

-- ---------------------------------------------------------------- contacts
CREATE TABLE contacts (
    id                  INTEGER PRIMARY KEY,
    company_id          INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    channel             TEXT NOT NULL,
    value               TEXT NOT NULL,
    normalized_value    TEXT NOT NULL,
    name                TEXT,
    role                TEXT,
    role_category       TEXT NOT NULL DEFAULT 'general',
    is_generic          INTEGER NOT NULL DEFAULT 1,
    source_type         TEXT NOT NULL,
    source_url          TEXT,
    retrieved_at        TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'unknown',
    quality_score       REAL NOT NULL DEFAULT 0,
    is_current          INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (company_id, channel, normalized_value)
);
CREATE INDEX ix_contacts_company ON contacts(company_id);
CREATE INDEX ix_contacts_value ON contacts(normalized_value);

CREATE TABLE contact_evidence (
    id           INTEGER PRIMARY KEY,
    contact_id   INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    source_url   TEXT NOT NULL,
    snippet      TEXT,
    webpage_id   INTEGER REFERENCES webpages(id) ON DELETE SET NULL,
    retrieved_at TEXT NOT NULL,
    UNIQUE (contact_id, source_url)
);

CREATE TABLE verification_results (
    id         INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    check_name TEXT NOT NULL,
    passed     INTEGER,
    detail     TEXT,
    checked_at TEXT NOT NULL
);
CREATE INDEX ix_verif_contact ON verification_results(contact_id);

-- ---------------------------------------------------------------- ranking
CREATE TABLE prospect_scores (
    id                  INTEGER PRIMARY KEY,
    company_id          INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    job_id              INTEGER REFERENCES search_jobs(id) ON DELETE SET NULL,
    total               REAL NOT NULL,
    industry            REAL NOT NULL,
    tasks               REAL NOT NULL,
    internship          REAL NOT NULL,
    contact             REAL NOT NULL,
    geography           REAL NOT NULL,
    activity            REAL NOT NULL,
    suitability         REAL NOT NULL,
    evidence_confidence REAL NOT NULL,
    outreach_readiness  REAL NOT NULL,
    readiness_label     TEXT NOT NULL,
    weights_json        TEXT NOT NULL,
    breakdown_json      TEXT NOT NULL,
    is_current          INTEGER NOT NULL DEFAULT 1,
    scored_at           TEXT NOT NULL
);
CREATE INDEX ix_scores_company ON prospect_scores(company_id, is_current);

-- ---------------------------------------------------------------- outreach
CREATE TABLE outreach_drafts (
    id           INTEGER PRIMARY KEY,
    company_id   INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    contact_id   INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    kind         TEXT NOT NULL,
    subject      TEXT,
    body         TEXT NOT NULL,
    edited_body  TEXT,
    facts_json   TEXT NOT NULL,
    method       TEXT NOT NULL,
    approved     INTEGER NOT NULL DEFAULT 0,
    approved_at  TEXT,
    generated_at TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX ix_drafts_company ON outreach_drafts(company_id);

CREATE TABLE outreach_history (
    id               INTEGER PRIMARY KEY,
    company_id       INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    contact_id       INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    status           TEXT NOT NULL,
    channel          TEXT,
    contact_value    TEXT,
    draft_id         INTEGER REFERENCES outreach_drafts(id) ON DELETE SET NULL,
    message_snapshot TEXT,
    event_date       TEXT NOT NULL,
    follow_up_date   TEXT,
    notes            TEXT,
    outcome          TEXT,
    recorded_at      TEXT NOT NULL
);
CREATE INDEX ix_outreach_company ON outreach_history(company_id, id);

CREATE TABLE do_not_contact (
    id         INTEGER PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    domain     TEXT,
    email      TEXT,
    reason     TEXT,
    added_at   TEXT NOT NULL,
    CHECK (company_id IS NOT NULL OR domain IS NOT NULL OR email IS NOT NULL)
);

-- ---------------------------------------------------------------- review & evaluation
CREATE TABLE review_queue (
    id               INTEGER PRIMARY KEY,
    item_type        TEXT NOT NULL CHECK (item_type IN ('possible_duplicate','qualification','identity','other')),
    company_id       INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    other_company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    score            REAL,
    reason           TEXT NOT NULL,
    details_json     TEXT,
    status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','dismissed')),
    resolution       TEXT,
    created_at       TEXT NOT NULL,
    resolved_at      TEXT
);
CREATE UNIQUE INDEX ux_review_queue ON review_queue(item_type, company_id, COALESCE(other_company_id, 0));

CREATE TABLE evaluation_reviews (
    id                            INTEGER PRIMARY KEY,
    company_id                    INTEGER NOT NULL UNIQUE REFERENCES companies(id) ON DELETE CASCADE,
    job_id                        INTEGER REFERENCES search_jobs(id) ON DELETE SET NULL,
    discovery_relevant            INTEGER,
    is_duplicate                  INTEGER,
    industry_correct              INTEGER,
    contact_source_valid          INTEGER,
    unsupported_claims            INTEGER,
    claims_checked                INTEGER,
    internship_assessment_correct INTEGER,
    outreach_useful               INTEGER,
    notes                         TEXT,
    reviewed_at                   TEXT NOT NULL
);

-- ---------------------------------------------------------------- caches
CREATE TABLE llm_cache (
    key           TEXT PRIMARY KEY,
    model         TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
