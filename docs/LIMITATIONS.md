# Known limitations & future improvements

## Known limitations (honest list)

**Coverage**
- Free, permitted sources do not list every company. OpenStreetMap misses many SMEs; Wikidata favours larger
  firms; GLEIF covers few UAE entities. Free-zone and chamber directories that do not permit automation are
  import-only. Expect to supplement with CSV/URL imports.
- The public Overpass servers are often overloaded; a search can report partial map coverage until it is resumed.
- There is no general web search unless you run your own SearXNG instance.

**Website research**
- Sites that need JavaScript are only partially readable without the optional Playwright renderer.
- Sites behind bot protection (Cloudflare/Vercel checks), logins or robots.txt disallow are recorded as
  not researchable — by design, they are never bypassed.
- Arabic-only pages are skipped when an English version exists and are otherwise only partially understood
  (the taxonomy is English).
- The crawler reads at most ~10 pages per site, so information deep in a site can be missed.

**Classification**
- Industry, business-type, eligibility and task detection are keyword/rule based. They are transparent and
  testable but can be fooled by unusual wording; every decision shows its evidence so you can judge it.
  Business-type signals from mentions of clients or investors are damped but not eliminated.
- Free-zone boundaries are approximated by a centre and radius; companies near a boundary may be classed as
  "nearby" rather than "in area". Emirate boundaries are simplified OSM polygons (≈ 300 m tolerance).
- Company size is only known when a source states it (Wikidata, the website, structured data); otherwise it
  stays *unknown*. Statements such as "team of 25 engineers" are treated as lower bounds.
- "Operational status" uses indicators (live site, recent content, hiring, multiple sources) and is a judgement,
  not a registry check.

**Contacts**
- DNS/MX checks show whether a domain accepts mail, never whether a specific mailbox exists (no SMTP probing).
- Named people are extracted only from simple team-page layouts; many sites will show none.
- Contact forms rendered by JavaScript are not detected without Playwright.

**Local AI**
- Optional and not installed during development: the Ollama integration is tested only against a mocked API.
  Quality depends on the model you install; outputs that cannot be verified against the page are discarded.

**Evaluation**
- No accuracy figures are claimed. Use the Review & Evaluation workflow on your first 25–50 real companies.

**Platform**
- Single-user, local application; Streamlit background jobs stop if the app process stops (they are then
  resumable).

## Future improvements

1. **Arabic support** — bilingual taxonomy terms and transliteration-aware name matching (also improves GLEIF matches).
2. **Self-hosted Overpass or OSM extract** — import a UAE `.osm.pbf` extract (Geofabrik) locally to remove
   dependence on overloaded public servers.
3. **Learned ranking from your reviews** — once enough manual evaluations exist, fit weights to your judgements
   (with cross-validation) instead of fixed rubric weights.
4. **Better people extraction** — layout-aware parsing of team pages (cards/grids) and JSON-LD `Person` coverage.
5. **Job-board-aware careers parsing** — read structured job postings (`JobPosting` JSON-LD, Lever/Greenhouse/Workable
   public JSON endpoints where permitted) for stronger observed-need signals.
6. **Calendar/email integration (opt-in)** — create follow-up reminders; still never send automatically.
7. **Exhibitor lists** — importers for permitted event exhibitor lists (e.g. Dubai Airshow, GITEX) as manual CSVs.
8. **Playwright by default for contact pages only** — targeted rendering where forms are commonly JavaScript-based.
9. **Scheduled refresh** — re-check qualified companies monthly for new vacancies or programmes.
