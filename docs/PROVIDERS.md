# Data sources (discovery providers)

Each source was investigated before an adapter was written (access checks run on **2026-10-11**). Sources that
do not permit automated collection are *not* automated; the registry still lists them, with the manual
alternative, so the Search → Data sources table explains every decision.

Every provider reports: enabled flag, availability, coverage, source URL, limitations, last successful run,
last error, result counts and (on demand) a health check — stored in `discovery_providers` and per job in
`provider_runs`.

## Automated (free, no key)

### OpenStreetMap — Overpass API (`osm_overpass`) · enabled
- **Access:** public Overpass instances; fair-use guidance is well under 10,000 queries / 1 GB per day; clients
  must identify themselves (User-Agent). Data © OpenStreetMap contributors, ODbL.
- **What ATLAS queries:** named `office=*`, `craft=*`, `industrial=*`, `man_made=works`, and industrial/factory
  buildings, in small bounding-box tiles over the requested area (zones use one small tile). Excludes
  embassies, government offices, estate agents, tailors, etc., and infrastructure (substations, gates…).
- **Reliability measures:** tiles are queried one-per-endpoint across several public instances, with failover,
  a per-endpoint circuit breaker, and a 3-day tile cache. Failed tiles are reported as *partial coverage*; re-run
  later to fill gaps.
- **Observed (2026-10-11):** Dubai's urban area has ~1,900 named office/industrial features, ~485 with a website.
  Public servers frequently answered 429/504; individual tiles sometimes failed even after retries.
- **Limitations:** many SMEs are unmapped; tags and websites are community-maintained.

### Wikidata — SPARQL (`wikidata`) · enabled
- **Access:** `query.wikidata.org`; Wikimedia's robot/User-Agent policy requires an identifying User-Agent with
  contact info (requests without one get HTTP 403). Data CC0. One query per search, cached 7 days.
- **What ATLAS queries:** organisations of verified business classes (business, company, enterprise,
  organisation, airline, software/technology company, public/private company, bank) with country = UAE, plus
  website, industry, HQ, coordinates and employee count when present.
- **Observed:** ~550 UAE organisations of these classes with English labels; skews to larger/notable firms.
- **Limitations:** community-edited; industries/employee counts may be outdated. Employee counts are used only
  as a size *band* with their source cited.

### GLEIF — LEI records (`gleif`) · identity checks always; discovery disabled by default
- **Access:** public REST API, no key, CC0, 60 requests/minute.
- **Use:** during research ATLAS looks up a matching legal entity (≥ 90 name similarity, country AE) and records
  its LEI, legal name and registration status as a `legal_entity` claim.
- **Limitations:** small UAE coverage, many Arabic-only names, no websites → low discovery yield.

## Requires your setup

### SearXNG (`searxng`) · disabled until configured
Your **own** SearXNG instance (e.g. `docker run -p 8888:8080 searxng/searxng`) with `search.formats: [html, json]`
enabled in `settings.yml`. Set the URL in Settings → Discovery. Public SearXNG instances and undocumented
search-engine endpoints are deliberately not used. Results are only website leads (job boards, directories and
social sites are filtered out) and are verified during research. *Tested with mocked responses only.*

## Import-only (manual)

| Source | Why not automated | How to use it |
|---|---|---|
| **CSV / XLSX import** (`manual_csv`) | — | Search → Import → upload; recognised columns include name, website, email, phone, address, city/emirate, industry/activity. |
| **Website URL list** (`url_list`) | — | Paste official websites; names are taken from the sites during research. |
| **Public directory page** (`directory_page`) | Terms vary per directory | Paste one public list page *after checking its terms*; ATLAS honours robots.txt and extracts outbound company-website links. |
| **DIFC Public Register** | difc.com serves a bot-protection checkpoint (HTTP 429 "Vercel Security Checkpoint") to automated clients and robots.txt disallows `/api/` and search paths. ATLAS never bypasses anti-bot systems. | Look companies up in your browser and import them. |
| **Dubai Pulse — DED licence datasets** | Downloads require a permission request, acceptance of terms and approval (up to 14 days). | Request access yourself; if the terms allow, import the CSV. |
| **Chamber & free-zone member directories** (Dubai Chamber, DMCC, JAFZA, DSO/Dtec…) | Interactive or login-gated applications; terms not confirmed to allow automated collection. | Use the directory-page importer for a public page whose terms you checked, or a CSV. |
| **LinkedIn** | User Agreement prohibits scraping. | Never fetched. ATLAS only stores a company's LinkedIn page link when the company's own site publishes it. |

## Not implemented (paid / key required)

Google Places API, Apollo, Hunter (paid) and OpenCorporates (key for approved projects) are listed in the
registry as unavailable. The core workflow does not depend on them.

## Adding a provider

1. Subclass `atlas.providers.base.DiscoveryProvider`, define `info: ProviderInfo` and `async discover(criteria, ctx)`
   returning `ProviderResult(candidates=[CandidateCompany…], scanned, notes, errors, partial)`.
2. Use `ctx.fetcher` for all HTTP (robots, rate limits, retries and caching come for free).
3. Add it to `IMPLEMENTED` in `providers/registry.py`, and write tests with mocked responses (see
   `tests/unit/test_providers.py`). Providers never write to the database — the pipeline handles dedup and provenance.
