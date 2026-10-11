# Troubleshooting

**OpenStreetMap says "partial coverage" / tiles failed.**
The public Overpass servers are shared and often overloaded (HTTP 429/504). ATLAS already fails over between
instances and retries. Successful tiles are cached for 3 days, so simply resume the job later (Search → Search
history → Resume) — only the missing tiles are fetched again. You can add or reorder endpoints in
Settings → Discovery.

**Wikidata returns HTTP 403.**
Wikimedia requires an identifying User-Agent with contact details. Make sure `http.contact_url` (Settings or
`config/atlas.toml`) is a real URL such as your GitHub profile.

**A job shows "interrupted".**
The process stopped (closed terminal, laptop sleep, Streamlit restart) while the job was running. Progress is
saved; press **Resume**. Completed sources and finished crawls are not repeated.

**"robots.txt could not be retrieved".**
The site's robots.txt answered with a server error (5xx). RFC 9309 says to treat that as "disallow all", so
ATLAS fetched nothing. It is often temporary — use **Re-research now** later.

**A company's website "could not be researched".**
Check the company profile → Sources & history → Pages researched. Common reasons:
- *robots.txt disallows* – the site does not permit automated access; research it manually.
- *bot-protection challenge* – the site blocks automated clients (e.g. Cloudflare/Vercel checks); ATLAS never bypasses these.
- *needs JavaScript* – install Playwright (`pip install -e ".[js]" && playwright install chromium`) and enable it in Settings → Crawler.
- *unreachable* – the site was down or slow; use **Re-research now** later.

**The wrong website was attached to a company.**
Company Profile → Corrections → set the official website (saved with highest confidence; the old value stays
in the field history), then **Re-research now**.

**Two records are the same company.**
Review & Evaluation → Possible duplicates → *Same company — keep left/right*. All sources are kept on the merged record.

**Drafts contain `[Your full name]` placeholders.**
Fill in Settings → Profile, then Outreach Studio → *Regenerate drafts*. Approved drafts are never overwritten.

**Local AI shows as unavailable.**
Install and start Ollama, pull the configured model (`ollama pull qwen2.5:7b-instruct`) and use
Settings → Local AI → *Test connection*. Everything works without it.

**Email marked "Invalid address".**
The domain has no mail routing in DNS (no MX/A record) or the syntax is wrong. Use the company's contact form or
careers page instead — the company stays in your list.

**"database is locked".**
Only one heavy writer should run at a time; ATLAS uses WAL mode and a 30-second busy timeout. If you started two
CLI jobs simultaneously, let one finish.

**Where are the logs?**
`data/logs/atlas.jsonl` — one JSON object per line (events such as `robots_blocked`, `http_retry`,
`overpass_endpoint_failed`, `prompt_injection_flags`).

**Start completely fresh.**
Stop the app and delete `data/atlas.db*` (keep `data/http_cache.db` to avoid re-downloading). Run `atlas init`.
