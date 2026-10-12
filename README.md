# ATLAS — Lead Generation Intelligence & Outreach Platform

ATLAS finds real companies in the UAE where a school-age student could realistically gain experience it, researches them from their own websites, explains *why* each one fits (with links to the
evidence), finds legitimate published contact channels, ranks them transparently, drafts personalised
outreach for you to review, and tracks your conversations.

It runs entirely on your machine, costs nothing, and never sends a message on your behalf.

> **Built for:** a 17-year-old A-level student in Dubai (Maths, Physics, Economics) looking for summer 2027
> work experience in aerospace, mechanical engineering, robotics, AI, software and quantitative technology.
> Everything is configurable for other profiles.

---

## What it does

| Stage | What happens | Where it lives |
|---|---|---|
| **Query** | Plain-English request → structured filters (location, industries, exclusions, size, skills…). You confirm/edit before anything runs. Optional local AI only fills gaps. | `src/atlas/query/` |
| **Discovery** | Real companies from OpenStreetMap (Overpass), Wikidata, optionally GLEIF and your own SearXNG; plus CSV/XLSX, URL-list and directory-page imports. | `src/atlas/providers/` |
| **Identity resolution** | Normalises names/domains/phones/addresses; merges only clear matches; uncertain ones (branch? parent/subsidiary?) go to a review queue. Provenance of every source is kept. | `src/atlas/resolve/` |
| **Research** | Polite, budgeted crawl of each official website (robots.txt, per-site delays, retries, caching, duplicate-page detection, bot-challenge detection). Extracts evidence-linked claims and published contacts. | `src/atlas/crawl/`, `src/atlas/extract/` |
| **Qualification** | Industry relevance (with look-alike detection, e.g. *car rental ≠ automotive engineering*), business type, location, size (never invented), activity → *qualified / rejected / needs review* with reasons. | `src/atlas/assess/qualification.py` |
| **Internship suitability** | *Confirmed opportunity / plausible speculative / eligibility unclear / explicitly ineligible* — never assumes a company accepts minors. | `src/atlas/assess/internship.py` |
| **Task intelligence** | Concrete supervised tasks you could offer, each backed by the company's own text; *observed needs* (e.g. a matching vacancy) are separated from *inferred opportunities*. | `src/atlas/assess/tasks.py` |
| **Contacts** | Published emails, contact forms, careers pages, named decision-makers — with DNS/MX checks and source verification. No guessed addresses, no SMTP probing. | `src/atlas/extract/contacts.py`, `src/atlas/contacts/` |
| **Ranking** | Transparent weighted rubric (weights editable) plus separate suitability, evidence-confidence, contact-quality and readiness scores. | `src/atlas/rank/scoring.py` |
| **Outreach** | Five draft types grounded in verified facts and your profile, with a "facts used" panel. You edit, approve and send them yourself. | `src/atlas/outreach/` |
| **CRM** | Status tracking, follow-ups, duplicate-outreach prevention, do-not-contact list. | `app/views/crm.py`, `src/atlas/outreach/crm.py` |
| **Export & evaluation** | CSV/XLSX (formula-injection safe); manual review workflow that measures accuracy on your first 25–50 companies. | `src/atlas/export/`, `src/atlas/evaluation/` |

## Quick start (one click)

1. Download: **Code → Download ZIP** on GitHub (or `git clone https://github.com/Yousuf6273/atlas`), and unzip.
2. Double-click **`start.command`** (macOS) or **`start.bat`** (Windows). On Linux run `./start.sh`.
3. Your browser opens ATLAS at http://localhost:8530. The first launch installs everything (needs Python 3.11+).

> macOS may say the file is from an unidentified developer: right-click `start.command` → **Open** the first time.

## Install (manual)

Requirements: macOS/Linux/Windows with **Python 3.11+** and internet access. No accounts or API keys.

```bash
cd ~/atlas
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
atlas init
```

Optional extras (not needed for the core workflow):

- **Local AI** – install [Ollama](https://ollama.com), then `ollama pull qwen2.5:7b-instruct`. ATLAS detects it automatically.
- **JavaScript-only websites** – `pip install -e ".[js]" && playwright install chromium`, then enable it in Settings → Crawler.

## Run the dashboard

```bash
atlas ui            # opens http://localhost:8530
```

(Equivalent: `streamlit run app/streamlit_app.py`.)

## Your first search

1. **Settings → Profile**: enter your name, school, email and skills (add concrete examples and real projects
   if you have them). Drafts use only what you write here; blanks become visible `[placeholders]`.
2. **Search**: type, for example,
   *“Find 50 aerospace, mechanical engineering, robotics, AI and software companies in Dubai that might accept a summer intern.”*
   Click **Interpret**, check the filters, then **Start search**. Progress, per-source status and a live
   activity log are shown; you can cancel and resume at any time.
3. **Prospect Explorer**: filter and sort by any score; select a row to open its **Company Profile** (evidence,
   task ideas, score breakdown, uncertainties, recommended approach).
4. **Outreach Studio**: edit, approve and copy a draft; send it yourself; then **record it as sent**.
5. **Pipeline (CRM)**: update statuses as replies arrive; follow-ups appear when due.
6. **Review & Evaluation**: review your first 25–50 companies to measure how accurate ATLAS is on real data.

Command-line equivalent:

```bash
atlas search "Find 30 robotics and automation companies in Dubai" --yes
atlas top -n 20
atlas export --format xlsx
atlas resume <job_id>      # after an interruption (Ctrl-C cancels safely)
atlas providers --check    # health-check the data sources
atlas reassess --job 1 --recrawl   # re-run extraction + assessments (pages come from the 14-day cache)
```

## Principles

- **Evidence or nothing.** Every important claim stores the URL and the quoted text it came from, plus the retrieval time.
- **Uncertainty is explicit.** Unknown size stays *unknown*; missing coordinates are never treated as "outside the area";
  unclear eligibility is labelled as such.
- **Legitimate sources only.** robots.txt is always obeyed; anti-bot pages, logins and CAPTCHAs are never bypassed;
  LinkedIn is never scraped; directories that don't permit automation are import-only (see `docs/PROVIDERS.md`).
- **No fabricated data.** No invented companies, people, emails, headcounts, skills or "company problems".
  Synthetic fixtures exist only in `tests/` and use reserved `.example` domains.
- **You stay in control.** Nothing is sent automatically; "contacted" is recorded only when you confirm you sent it.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) · [Database schema](docs/DATABASE.md) · [Data sources](docs/PROVIDERS.md)
- [Testing & evaluation](docs/TESTING.md) · [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Known limitations & future work](docs/LIMITATIONS.md)
- Example configuration: [`config/atlas.example.toml`](config/atlas.example.toml)

## Data licences

OpenStreetMap data © OpenStreetMap contributors (ODbL 1.0) — including the simplified emirate boundaries in
`src/atlas/geo/data/`. Wikidata and GLEIF data are CC0. Company website content remains the property of its
owners; ATLAS stores short quoted snippets as evidence for your personal research.
