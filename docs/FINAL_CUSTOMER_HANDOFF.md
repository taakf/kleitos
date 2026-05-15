# Axion — Final Customer Handoff

A one-page, practical checklist for receiving and running Axion. For
deeper detail follow the cross-references — this page does not repeat
them.

- Install / run options → [`INSTALL.md`](../INSTALL.md)
- Day-to-day usage → [`docs/CUSTOMER_QUICKSTART.md`](CUSTOMER_QUICKSTART.md)
- Common questions → [`docs/CLIENT_FAQ.md`](CLIENT_FAQ.md)
- Honest limitations → [`KNOWN_LIMITATIONS.md`](../KNOWN_LIMITATIONS.md)

---

## What Axion is

A **local, single-machine** portfolio-intelligence application. It runs
on `127.0.0.1` (loopback only), stores everything on your own disk, and
is not a hosted service. The release channel is `local` — confirm this
in the `RELEASE_MANIFEST.json` bundled in the zip, or on
**Settings → Diagnostics**.

It is **not** investment advice. Insights are operational signals
(concentration, calendar clusters, data gaps, factor touchpoints)
grounded in your stored portfolio rows.

---

## Handoff checklist

### 1. Unzip
- [ ] Unzip `axion-macos.zip` or `axion-windows.zip` to a folder you own.
- [ ] Open `RELEASE_MANIFEST.json` — it records app version, release
  channel, git commit, and build timestamp, and confirms no database
  files and no API keys are bundled.

### 2. Run
- [ ] **macOS / Linux:** `./scripts/run_local.sh` in a terminal.
  `Axion.app` is also included for Finder users; because it is **not
  code-signed**, the first launch needs right-click → **Open** to clear
  Gatekeeper.
- [ ] **Windows:** double-click `Axion.bat`, or
  `PowerShell -ExecutionPolicy Bypass -File scripts\run_local.ps1`.
- [ ] The launcher creates a virtual environment, runs database
  migrations, and opens the dashboard at `http://127.0.0.1:7777/dashboard/`.
  Set `AXION_PORT` to use a different port.

### 3. Import a portfolio
- [ ] **Portfolio → Holdings → Upload** and pick a CSV (see
  `sample_portfolio.csv`), or **+ Add** a holding manually.
- [ ] CSV import works fully offline — no AI or internet required.

### 4. Configure optional keys (optional)
- [ ] **Settings → AI Configuration** — add an Anthropic, OpenAI/ChatGPT,
  or Google Gemini key to unlock optional AI features. Axion works
  without any AI key; deterministic mode is a normal mode, not an error.
- [ ] **Settings → News Sources** — bundled RSS feeds need no key.
  `NEWSAPI_KEY` and `FINNHUB_KEY` are optional; *Missing key* is an
  expected state, not a failure.
- [ ] Keys are stored at `~/.axion.env` (permissions `600`) and never
  leave your machine except to call the provider you configured.

### 5. Collect news
- [ ] News is collected from public RSS / regulatory feeds on a
  schedule. Use **Insights → News** to browse and filter it.
- [ ] **Events** (top-level tab) is a separate surface — the scheduled
  corporate-events calendar (earnings, dividends, AGMs). Corporate
  events are loaded via the **Import CSV** drawer.

### 6. Upload revenue geography (optional)
- [ ] **Portfolio → Exposures** shows a **Listing country** card
  (automated from each instrument's ISIN / venue) and a separate
  **Revenue geography** card.
- [ ] Revenue geography is populated only from a CSV you upload (or a
  reviewed AI extraction you confirm). Axion never infers it from
  listing country, ISIN, or sector.

### 7. Generate a support bundle if something fails
- [ ] Run `python scripts/support_bundle.py`. It writes a redacted zip
  to `~/axion-data/support/`.
- [ ] The bundle includes app version, OS info, schema version, table
  counts, source-health summary, and the last 200 KB of each log.
- [ ] It **excludes** your database, backup files, raw `.env`, API keys,
  and holdings. Attach that single zip when asking for help.

### 8. Know the limitations
- [ ] **No live market-price feed.** Axion does not stream or display
  live prices; it works from imported holdings, collected news, and
  scheduled corporate events.
- [ ] **No broker sync, no OAuth.** There is no broker connection and no
  Google / Microsoft account linking. OAuth is roadmap-only — see
  [`docs/OAUTH_ROADMAP.md`](OAUTH_ROADMAP.md).
- [ ] **No paid-vendor data bundled.** Bloomberg / FactSet / Refinitiv /
  S&P Capital IQ are not included.
- [ ] **ATHEX automation is unsupported** — Athens Exchange has no
  stable public machine-readable corporate-events feed, so corporate
  events use the CSV import path.
- [ ] **Insights are deterministic-first.** AI narration is optional and
  grounded — it can reword cards but never add new holdings, numbers,
  or claims. Insights are operational signals, not investment advice.

---

## Data, backups, and safety

- Your data lives in `~/axion-data/` (older installs may use
  `~/kleitos-data/`). Settings and keys live in `~/.axion.env`.
- Every schema migration first writes a consistent backup to
  `~/axion-data/backups/` before any change is applied.
- If the app reports your data was created by a newer version, your data
  is intact — update Axion or restore an older backup. See
  [`docs/CUSTOMER_QUICKSTART.md`](CUSTOMER_QUICKSTART.md) for the exact
  recovery steps.

---

## Navigation reference

| Top tab | Purpose |
|---------|---------|
| **Portfolio** | Holdings, Exposures (Listing country + Revenue geography), Trades |
| **Insights** | Overview, News, Analysis, Digest, Inbox |
| **Events** | Corporate-events calendar (earnings / dividends / AGMs) |
| **Alerts** | Concentration breaches, calendar clusters, stale-data warnings |
| **Assistant** | Conversational queries (needs an AI provider) |
| **Settings** | API keys, providers, source health, diagnostics |

*News* (Insights → News) is the news feed. *Events* (top-level tab) is
the corporate-events calendar. They are deliberately separate surfaces.
