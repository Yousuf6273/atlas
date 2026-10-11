# Testing & evaluation

## Running the tests

```bash
source .venv/bin/activate
pytest                       # unit + integration tests, no network (≈ 2 minutes)
pytest tests/unit -q         # fast unit tests only
ATLAS_LIVE_TESTS=1 pytest tests/integration/test_live_providers.py -s   # real services (network)
```

All non-live tests use mocked HTTP (`respx`) and a stubbed DNS resolver; they never touch the network.

## What is covered

| Area | Tests |
|---|---|
| Database & migrations | `tests/unit/test_db.py` — idempotent migrations, constraints, job lifecycle, stale-job recovery, higher-confidence values protected + history, provenance through merges, claim retirement, versioned assessments, contact upserts, do-not-contact, approved drafts never overwritten, review-queue uniqueness, settings overlay, partial provider runs re-run on resume |
| HTTP layer | `tests/unit/test_fetcher.py` — robots allow/disallow, RFC 9309 4xx/5xx semantics, Crawl-delay, retries/backoff, Retry-After, no retry on 404, redirects with robots per hop, content-type checks, size caps, cache hits, in-flight de-duplication, charset handling |
| Normalisation | `tests/unit/test_normalize.py` — legal suffixes, branches, public-suffix domains, URLs, phones (E.164), addresses, control characters |
| Providers | `tests/unit/test_providers.py` — Overpass query/tiling/conversion/exclusions/failover/partial coverage, Wikidata area & industry filters (incl. zone-level evidence), GLEIF lookup, SearXNG config & filtering, CSV/XLSX and URL imports, relevance look-alikes |
| Parsing & extraction | `tests/unit/test_extraction.py` — URL canonicalisation and page classification, HTML structure, bot-challenge/placeholder detection, email finding & obfuscation, role vs individual mailboxes, people + roles, contact forms, careers pages, **no guessed emails**, claims with provenance, eligibility rules (requirement vs audience, terms-of-use ages, foreign programmes), size statements (years ≠ headcount), slogans ≠ offerings, **prompt-injection text produces no claims** |
| Query & matching | `tests/unit/test_query_and_matching.py` — the brief's example queries, exclusions, modifiers as keywords; merge/review/new rules (same domain, branches, parent/subsidiary, phones, ambiguity) |
| Engines | `tests/unit/test_engines.py` — industry rubric (monotonic, negative look-alikes, template repetition counts once), qualification statuses & geography (unknown ≠ outside; zone strictness; structured HQ city), size never invented, all four internship categories, tasks need company evidence and student skills, observed vs inferred wording, email verification statuses (never claims unchecked routing), contact quality & channel strategy, scoring rubrics/weights/readiness, grounded drafts, CRM rules |
| Local AI | `tests/unit/test_llm.py` — Ollama absent → fallback, missing model, schema-constrained output with retries & cache, give-up, sanitising/wrapping, **claims without verbatim quotes are dropped**, polished drafts with new facts rejected, query refinement only fills gaps |
| Export | `tests/unit/test_export.py` — formula-injection escaping, numeric columns, no formulas in XLSX |
| End to end | `tests/integration/test_pipeline_e2e.py` — full job on synthetic websites + mocked APIs: dedup with provenance, robots/binary skipping, qualified/rejected/review outcomes, ineligible programme, distributor + injection site, ranking, drafts with facts, export; resume without repeating work; clean cancellation then resume |

### Synthetic fixtures

`tests/fixtures/synthetic/` contains **fictional** companies on reserved `.example` domains (see its README).
They are served only through mocked HTTP inside tests and are never written to a real ATLAS database.

## Evaluating on real data (the 25–50 company review)

Automated tests show the code does what it is designed to do; they cannot show how accurate it is on the real
web. That is measured by you:

1. Run a real search (e.g. the first use case in the README).
2. Open **Review & Evaluation → Evaluation**, choose the job and a sample size (25–50; the highest-ranked companies).
3. For each company, check the website yourself and answer: relevant? duplicate? industry correct? contact really
   published at the cited source? how many claims did you check and how many were unsupported? internship
   assessment reasonable? outreach recommendation useful?
4. Metrics appear on the same page and in **Analytics**, each with its sample size and a 95 % Wilson interval:
   discovery relevance, duplicate rate, industry-classification accuracy, contact-source validity,
   unsupported-claim rate, internship-assessment agreement and useful-outreach rate.

ATLAS reports **no** accuracy figures until you have reviewed companies.

## Verification status (as of 2026-10-11)

| Capability | Status |
|---|---|
| OpenStreetMap / Overpass discovery | **Live-tested** — Dubai (35 tiles, ~2,000 features scanned; some tiles failed under server load and were reported as partial) and Dubai Silicon Oasis |
| Wikidata discovery | **Live-tested** — ~550 UAE organisations scanned per query |
| GLEIF legal-entity lookup | **Live-tested** (e.g. Dubai Aerospace Enterprise LEI found); discovery mode tested with mocks only |
| Website crawling, robots.txt, extraction, identity checks | **Live-tested** on ~100 real company websites, plus mocked synthetic sites |
| DNS/MX verification | **Live-tested** on real company domains; stubbed in automated tests |
| Qualification / internship / tasks / scoring / drafts | **Live-run** on real data (outputs inspected by hand; several calibration bugs found and fixed — see regression tests). Accuracy **not yet measured**: use the evaluation workflow |
| Streamlit dashboard | **Manually exercised** in a browser (all pages load without errors against real data; live job progress panel) |
| CLI (search, resume after cancel, reassess, export) | **Live-tested** |
| SearXNG provider | **Mocked only** (no instance available) |
| Ollama / local AI | **Mocked only** (Ollama not installed during development) |
| Playwright rendering | **Untested** (optional dependency not installed) |
| Directory-page importer | **Mocked only** for the extraction logic (no directory was used live, to avoid assuming any site's terms) |
