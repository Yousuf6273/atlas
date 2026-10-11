# Architecture

ATLAS is a modular Python package (`src/atlas`) with a Streamlit UI (`app/`) and a CLI (`atlas`).
All state lives in one SQLite database (`data/atlas.db`) plus an HTTP response cache (`data/http_cache.db`).

```
             ┌──────────── Streamlit UI (app/) ─────────────┐        ┌─── CLI (atlas.cli) ───┐
             │ Overview · Search · Explorer · Company ·      │        │ search · resume · top │
             │ Contacts · Outreach · CRM · Analytics · Review│        │ export · providers    │
             └───────────────┬──────────────────────────────┘        └──────────┬────────────┘
                             │ reads (queries.py) / starts jobs (pipeline.runner)│
                             ▼                                                   ▼
   ┌──────────────────────────────── pipeline.orchestrator.run_job ───────────────────────────────┐
   │ 1 discovery ──► 2 dedup/ingest ──► 3 selection ──► 4 research ──► 5 assessment ──► stats     │
   │   providers/     pipeline/ingest     priority by      pipeline/      pipeline/assess_company  │
   │                  resolve/matcher     relevance        research       assess/* contacts/* rank │
   └───────────────────────────────────────────────┬──────────────────────────────────────────────┘
                                                   │ every step persisted (resumable)
                                                   ▼
                               SQLite (db/*) — provenance, claims, versioned assessments
```

## Packages

| Package | Responsibility |
|---|---|
| `config` | Pydantic settings: defaults → `config/atlas.toml` → Settings-page overrides (`db.settings.effective_config`). |
| `models` | Pydantic domain models and enums (criteria, candidates, evidence/claims, assessments, contacts, profile). |
| `db` | Migrations (`db/migrations/*.sql`) and plain-function repositories per aggregate; `queries.py` holds UI read models. |
| `geo` | UAE gazetteer (emirates + free zones, checked against Nominatim) and point-in-polygon emirate lookup (OSM boundaries). |
| `query` | Deterministic natural-language parser; `llm.tasks.llm_refine_query` optionally fills gaps. |
| `providers` | `DiscoveryProvider` interface + adapters (Overpass, Wikidata, GLEIF, SearXNG, manual imports) and a registry that also lists sources deliberately *not* automated. |
| `net` | `Fetcher`: async httpx client with robots.txt (RFC 9309), per-host rate limiting/Crawl-delay, bounded concurrency, retries with exponential backoff + jitter and Retry-After, manual redirects (robots per hop), size caps, content-type checks, in-flight de-duplication, SQLite response cache. TLS uses the operating-system trust store (`truststore`) so sites with incomplete certificate chains work as in a browser, with verification still enforced. |
| `crawl` | URL canonicalisation & page-type classification; HTML parsing/cleanup; budgeted focused site crawler; optional Playwright renderer. |
| `extract` | Contact extraction (emails incl. simple obfuscation, phones, forms, careers pages, named people + roles, LinkedIn page links) and evidence-linked signal claims (activities, offerings, industry/business-type terms, internships, eligibility, hiring roles, size, location, recency). Instruction-like text aimed at automated readers is discarded and reported. |
| `resolve` | Normalisation (names, legal suffixes, domains via public-suffix list, phones E.164, addresses) and the entity matcher. |
| `assess` | Dossier loader; taxonomy; qualification; internship suitability; task-opportunity templates. |
| `contacts` | DNS/MX email verification and the contact-quality rubric / best-channel strategy. |
| `rank` | Transparent scoring rubrics and separate suitability / evidence / contact / readiness dimensions. |
| `outreach` | Grounded draft generator and CRM rules (confirmation, duplicates, do-not-contact). |
| `llm` | Optional Ollama client (JSON-schema structured output, validation + retries, cache), prompt-injection guard, LLM-assisted tasks with verification. |
| `pipeline` | Context, ingest, website identity/resolution, research, assessment, orchestration, background runner. |
| `export` | CSV/XLSX with formula-injection escaping. |
| `evaluation` | Metrics computed only from the student's manual reviews (with Wilson intervals). |

## Key design decisions

**Deterministic first.** Every judgement (relevance, business type, eligibility, tasks, scores) is a rule over
stored evidence, documented as a rubric in the module docstring. The LLM is optional and confined to
(a) suggesting filters for vague queries, (b) summarising activities — where each statement must carry a
quote that is found verbatim on the page or it is dropped — and (c) guarded draft polishing that is rejected if
it introduces any new number or proper noun.

**Evidence as data.** Extraction produces `extracted_claims` rows: *(type, value, quoted snippet, source URL,
retrieval time, extractor, confidence, is_inference)*. Assessments are pure functions of a `Dossier`
(company + sources + locations + pages + claims + contacts) loaded from the DB, so they can be recomputed at
any time (e.g. new weights → `rescore_all`, no network).

**Never lose information.** Company fields carry source confidences; `db.companies.set_field` refuses to
overwrite a higher-confidence value but records the attempt in `field_history`. Assessments and scores are
versioned (`is_current`), claims are retired rather than deleted, merges move all provenance to the surviving
record and leave a `merged_into_id` pointer.

**Resumable jobs.** Provider runs, crawl jobs and assessments are persisted as they complete. A re-run skips
completed providers and finished crawls. Jobs left "running" without a heartbeat are marked *interrupted* at
start-up and can be resumed. Cancellation is cooperative (DB flag checked between steps; `TaskGroup` cancels
sibling tasks).

**Goal, not quota.** Companies are researched in priority order in batches until the number of *qualified*
companies reaches the target or the research budget is exhausted. Thresholds are never relaxed to hit a number.

**Politeness and compliance.** robots.txt cannot be disabled. 5xx/unreachable robots.txt ⇒ treated as
disallow-all (RFC 9309). Bot-protection pages are detected and recorded, never bypassed. APIs are called with an
identifying User-Agent and responses are cached (Overpass tiles 3 days, Wikidata 7 days).

## Data flow for one company

1. **Ingest** (`pipeline/ingest.py`): match against existing companies (domain/phone/name blocking → `resolve.matcher.decide`);
   merge, create, or create + enqueue review; store source provenance, locations and structured contacts.
2. **Research** (`pipeline/research.py`): resolve a website if missing (optional, verified), crawl ≤ N pages,
   store pages (text + hash), extract claims and contacts, record site status, check identity, look up GLEIF.
3. **Assess** (`pipeline/assess_company.py`): qualification → contact verification & quality → internship →
   tasks → best channel → score → review-queue items → drafts (not for rejected / do-not-contact companies).

## Threads and processes

The Streamlit app starts jobs in daemon threads (`pipeline/runner.py`), each with its own event loop and SQLite
connection (WAL mode allows concurrent UI reads). The UI polls progress from the DB with `st.fragment(run_every=2s)`.
The CLI runs jobs in the foreground; Ctrl-C requests cancellation and the job stays resumable.
