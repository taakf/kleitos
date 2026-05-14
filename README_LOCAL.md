# Axion — Local install

This is the **one** supported path to run Axion as a local application on a single machine. No Docker, no cloud, no admin rights.

## Requirements

- **Python 3.11 or newer** (3.12 recommended)
- **Internet access** for the first run (pip installs dependencies) and ongoing news collection
- About **300 MB of disk** for the venv + dependencies

## macOS / Linux

From the project root:

```bash
./scripts/run_local.sh
```

That's it. The first run takes 1–2 minutes (creating a venv and installing dependencies). After that, startup is a few seconds.

When the server is healthy, the dashboard opens automatically (macOS). On Linux, open <http://127.0.0.1:7777/dashboard/> manually.

Stop with **Ctrl+C** in the terminal.

> **macOS users — which path do I pick?** This repo also ships `Axion.app` for Finder users. Both work and both share `~/axion-data` for data. The **terminal launcher (`scripts/run_local.sh`) is the supported customer path** — it's verified by the smoke test and has the smaller blast radius (no `/Applications` install, no launchd auto-start, no code-signing gymnastics). `Axion.app` is an extra option for users who prefer double-clicking from Finder, but it is **not code-signed** so first launch needs right-click → Open to clear Gatekeeper.

## Windows

From a PowerShell window in the project root:

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\run_local.ps1
```

Or double-click **`Axion.bat`** in the project root for a guided launch (it ends up running the same flow plus a tray app if its dependencies are installed).

When the server is healthy, the dashboard opens automatically.

Stop with **Ctrl+C** in the PowerShell window.

## What it does

1. Verifies Python 3.11+.
2. Creates `.venv/` in the project root if missing.
3. Installs `requirements.txt` into the venv.
4. Creates `~/axion-data/` (or `~/kleitos-data/` if it already exists, for backward compatibility).
5. Runs database migrations against `~/axion-data/db/kleitos.db`.
6. Starts FastAPI/uvicorn on **`127.0.0.1:7777`** (loopback only — never exposed to the network).
7. Opens the dashboard in your browser.

## Common options

| Want to… | How |
|----------|-----|
| Use a different port | `AXION_PORT=7778 ./scripts/run_local.sh` |
| Use a different data dir | `AXION_DATA_DIR=/tmp/axion-test ./scripts/run_local.sh` |
| Stop the server | Ctrl+C in the launcher window |
| Reset to a clean state | See [docs/DEMO_RESET.md](docs/DEMO_RESET.md) |

## After it's running

- **Dashboard:** <http://127.0.0.1:7777/dashboard/>
- **API docs:** <http://127.0.0.1:7777/docs>
- **Health:** <http://127.0.0.1:7777/api/v1/health>

To import a portfolio, use the Portfolio tab in the dashboard or upload `sample_portfolio.csv` from the project root for a quick start.

## News sources

Out of the box, Axion collects from public RSS feeds — **no keys required**. The default-enabled sources are:

- Federal Reserve press releases
- European Central Bank press releases
- Google News Business
- WSJ Markets
- MarketWatch Top Stories
- Seeking Alpha Market News
- Investing.com News

Open **Settings → News Sources** to see live status for each one. Status follows the normalized vocabulary:

| Status | What it means |
|--------|---------------|
| **Active** | Source is enabled and the last fetch succeeded. |
| **Disabled** | You (or the config default) turned the source off. |
| **Missing key** | The source requires a user-provided API key. Set the env var listed in the **Auth env var** column and restart. |
| **Degraded** | The source responded but returned no items in the last run. |
| **Rate limited** | The source returned HTTP 429 or hit the configured per-source ceiling. |
| **Unreachable** | DNS, timeout, or 5xx response. Usually a network or vendor outage. |
| **Parser error** | The source replied but the parser couldn't extract items — content shape changed. |
| **Unsupported** | The source is declared in config but its parser isn't implemented in this build (the toggle is disabled). |
| **Misconfigured** | Source config is invalid — wrong auth type, env var, or required field. |
| **Error** | Anything else; the launcher log and `axion-server.log` have the scrubbed details. |

### Optional API-key sources

| Source | Env var | Notes |
|--------|---------|-------|
| **NewsAPI Business** (`newsapi-general`) | `NEWSAPI_KEY` | Free tier ~100 requests/day, development use. <https://newsapi.org/> |
| **Finnhub Market News** (`finnhub-news`) | `FINNHUB_KEY` | Free tier with generous rate limit. <https://finnhub.io/> |

Once the env var is set in `~/.axion.env` and you restart Axion, enable the source from **Settings → News Sources** and run a collection cycle.

**Subscription / paid sources** (Bloomberg, FactSet, Refinitiv, S&P Capital IQ, etc.) are **not** included in this build. Their integration is tracked in [docs/OAUTH_ROADMAP.md](docs/OAUTH_ROADMAP.md).

**ATHEX corporate-events calendar** is **not** part of this build — see [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md).

### Working with the News tab

The **Insights → News** tab is the inspection surface for everything the collectors pulled in. Each row carries traceability chips so you can tell at a glance how Axion connected the story to your portfolio:

- **Linked** — the story matched at least one of your holdings (direct ticker hit or factor channel).
- **Macro signal** — the macro-factor classifier flagged it with a deterministic factor (interest rates, oil, FX, etc.). Click the row to see the chain.

The filter bar above the table works server-side:

- **Search** debounces and queries the backend across title + summary.
- **Source / Type / Factor / Materiality** narrow the slice. Materiality is a **≥** filter (picking "High & above" includes High and Critical).
- **24h / 7d / 30d / All** pills set the published-at window.
- **Linked holdings only** drops everything that didn't match a holding — useful when you want only the items that affect what you own.
- **Reset** clears every filter back to default.

Saved Views capture the full filter shape, so a saved "News · Linked only · Macro signal" view restores into the exact same slice next time. URLs are scrubbed of `apiKey=` / `token=` / `Bearer …` style secrets before they ever reach the dashboard.

## AI features (optional)

Axion's core (portfolio management, exposures, alerts, source collection, deterministic risk rules) runs without any AI provider.

If you add a key for one of the three supported providers in **Settings → AI Provider**, you also get LLM-enhanced impact scoring, daily narrative digests, the conversational Assistant tab, and AI vision extraction for scanned-PDF portfolio imports.

| Provider | Env var | Key format | Customer label |
|----------|---------|------------|----------------|
| Anthropic (Claude) | `ANTHROPIC_API_KEY` | `sk-ant-…` | Anthropic (Claude) |
| OpenAI / ChatGPT | `OPENAI_API_KEY` | `sk-…` or `sk-proj-…` | OpenAI / ChatGPT |
| Google Gemini | `GOOGLE_API_KEY` | `AIza…` | Google (Gemini) |

Pick one as **Primary**. Optionally set a different **Backup** so Axion retries on rate-limit / 5xx / auth failures. Each saved key has a **Test** button that sends one minimal request and reports a typed status:

- **Active** — the provider responded.
- **Not configured** — no key for that provider.
- **Invalid key** — the provider rejected the key.
- **Quota / rate-limit** — billing or per-minute cap.
- **Unreachable** — network or vendor outage.
- **Misconfigured** — the provider SDK is not installed.
- **Error** — anything else; the underlying exception is logged but never returned in the UI message.

Keys live in `~/.axion.env` with `600` permissions. They never leave your machine except to call the provider you configured. Logs, diagnostics endpoints, and the support bundle (`scripts/support_bundle.py`) redact key-shaped strings before writing them anywhere.

> **OAuth is not part of this build.** Axion does not yet integrate with brokers, Google/Microsoft accounts, or any OAuth-authenticated data source. See [docs/OAUTH_ROADMAP.md](docs/OAUTH_ROADMAP.md) for the future plan.

## Data, backups, and upgrades

| Item | Location |
|------|----------|
| Database, logs, exports | `~/axion-data/` (or legacy `~/kleitos-data/`) |
| API keys and settings | `~/.axion.env` |
| Source allowlist | `config/sources.yaml` (in the project folder) |
| Pre-upgrade safety backups | `~/axion-data/backups/kleitos-pre-v<N>-<timestamp>.db` |

When you launch a newer Axion build against an older database, the launcher **automatically creates a safety backup before applying any schema change** — same data dir, `backups/` sub-folder, named `kleitos-pre-v<schema-version>-<YYYYMMDD-HHMMSS>.db`. The backup is a consistent snapshot via SQLite's `Connection.backup()`, not a raw file copy, so it's safe to use directly.

If the backup write fails (disk full, permissions), the launcher refuses to migrate and tells you why. Your live database is left untouched.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Python 3.11 or newer is required` | Install from <https://www.python.org/downloads/> and re-run. On Windows, tick **Add Python to PATH**. |
| `Port 7777 is in use` | The launcher names the process (e.g. `node (pid 12345)`). Either stop it, or set a different port: `AXION_PORT=7778 ./scripts/run_local.sh` on macOS/Linux, `$env:AXION_PORT='7778'; PowerShell -ExecutionPolicy Bypass -File scripts\run_local.ps1` on Windows. |
| Dashboard shows "degraded" | Open <http://127.0.0.1:7777/api/v1/health> for details — usually means the scheduler hasn't completed its first cycle yet (give it a minute). |
| **"Your Axion data was created by a newer version"** | This build's schema is older than your DB. Update Axion, or restore an older backup from `~/axion-data/backups/`. Your data is unchanged. |
| **"Axion could not open the database"** | DB file is corrupt or unreadable. Axion does **not** delete or overwrite it. Restore a backup, or move the file aside and relaunch for a fresh DB. |
| **"Pre-migration backup failed"** | Free disk space or fix folder permissions on `~/axion-data/backups/`, then relaunch. No schema change was applied. |
| Want a totally fresh start | See [docs/DEMO_RESET.md](docs/DEMO_RESET.md). |
| Want to verify the install is correct | `python scripts/smoke_local.py` runs 16 end-to-end checks against a temp DB. |
| Want a programmatic recovery / diagnostics check while the server is up | `curl http://127.0.0.1:7777/api/v1/system/recovery` (recovery only) or `/api/v1/system/diagnostics` (full structured snapshot, no secrets). |
| Need to send Axion's state to support | `python scripts/support_bundle.py` — creates a redacted zip at `~/axion-data/support/`. |

## Logs

The launcher writes to `~/axion-data/logs/`:

| File | What's in it |
|------|---|
| `axion-launcher.log` | Each launch (timestamp, project root, port, every stage line) |
| `axion-server.log` | uvicorn stdout/stderr — the actual server log |
| `axion-migration.log` | Output of `scripts/migrate.py` per launch |

Files grow until they reach 5 MiB, at which point the launcher rotates them (keeping up to 5 historical files per name as `axion-server.log.1`, `.2`, …). Nothing automated deletes log content, only ages it out.

## Support bundle

When something goes wrong, run:

```bash
python scripts/support_bundle.py
```

This produces a single zip at `~/axion-data/support/axion-support-<YYYYMMDD-HHMMSS>.zip` that you can attach to a support email. It includes:

- App + platform info (git commit, Python version, OS)
- Schema version + table counts
- Source registry counts (no API keys)
- Last 200 KB of each log file
- List of backup filenames (not the backup files themselves)
- Redacted environment + redacted settings

It does **not** include: your database file, any backup `.db` file, the raw `.env`, any API keys, holdings values, or portfolio names. Secrets matching common patterns (Anthropic `sk-ant-*`, OpenAI `sk-*`, Telegram bot tokens, etc.) are also redacted by value, not just by env-var name.

## Terminology

Customer-facing terms that are easy to confuse — Axion uses them with these specific meanings:

| Term | What it means in the UI | Where you see it |
|------|-------------------------|------------------|
| **News** | Items collected from public news / regulatory / RSS / API sources (Fed press releases, market news, etc.). Each one is a record in the backend `events` table. | Dashboard → **Insights → News** sub-tab. |
| **Insights** | The analysis layer that consumes your portfolio + news + alerts + relationships. Includes News, Analysis, Digest, and Inbox sub-tabs. | Dashboard → top-level **Insights** tab. |
| **Events** | Scheduled corporate / issuer events — earnings dates, dividends, AGMs, corporate actions. Phase 9 targets ATHEX-listed holdings first; new sources will follow. Backed by the `corporate_events` table. | Dashboard → top-level **Events** tab (separate from Insights → News). |
| **Listing country** | The country whose CSD issued the instrument's ISIN (or the exchange you trade it on). **Not** the company's revenue geography. The "Listing country" exposure card on the Portfolio tab uses this. | Portfolio → Exposures → **Listing country** card. |
| **Revenue geography** | Where the issuer actually earns money. Populated by **operator-uploaded CSV** (or, in a future phase, AI-extracted from annual reports). Axion **never infers** revenue geography from listing country, ISIN, sector, or any other proxy. | Portfolio → Exposures → **Revenue geography** card (separate from Listing country). |

The repo's backend keeps the table name `events` for News (matching the existing schema, API routes, and migrations); the customer-facing label for those rows is "News" / "news item". The new top-level **Events** tab is a separate surface backed by `corporate_events` and accessed via `/api/v1/corporate-events`.

### Listing country vs. Revenue geography

Phase 10 introduces a clean separation between two questions that used to share a single "Geography" column:

* **Where is this instrument listed?** — answered automatically from the ISIN prefix / venue / ticker suffix. The **Listing country** card on the Exposures tab is built from this.
* **Where does the company earn revenue?** — answered **only** when an operator uploads a regional breakdown via the **Revenue geography → Import CSV** drawer (or via `POST /api/v1/exposures/revenue-geography/import`). With no upload, the card honestly reports *"No revenue geography uploaded yet"* and the AI prompts say so too. There is **no fallback** from listing country.

CSV columns (case-insensitive): `region`, `revenue_share`, at least one of `ticker` / `isin`. Optional: `country`, `company_name`, `fiscal_year`, `period`, `currency`, `source_name`, `source_url`. `revenue_share` accepts `0.45`, `45`, or `45%`. Per-row errors are returned without aborting the batch; per-company sum-to-100 % checks emit soft warnings (a row that totals 87 % is kept and the leftover flows to *Other / unallocated*).

Holdings without any upload appear in the **Holdings without revenue breakdowns** panel under the Revenue geography card and as a `Revenue geography not uploaded` bucket on the chart, so the totals always sum to ≈100 % of the portfolio without inventing data.

#### Manual CSV vs AI extraction

The Revenue-geography card's **Add revenue geography** dialog has two tabs:

* **Manual CSV** — the primary, always-supported path. Same columns and rules described above. Works without any AI provider key.
* **AI extract from report** — optional, review-first. Upload a PDF annual report (or paste the regional-revenue passage) and Axion calls the configured AI vision provider with a strict anti-hallucination prompt. The dialog shows you the candidate rows in an editable table; **nothing is saved until you click *Confirm***. AI extraction requires an API key in Settings → AI Configuration; without one the tab reports a clean `missing_key` status and the Manual CSV tab stays usable.

What the AI extractor will *not* do:

* It will not infer revenue geography from headquarters, listing exchange, ISIN prefix, customer names, or country of incorporation.
* It will not invent percentages when the document has only narrative text.
* It will not persist anything without an explicit operator confirmation.
* The PDF bytes are processed entirely in memory and never written to disk.

### Working with the Events tab

The Events tab opens on a monthly calendar; each day shows compact chips coloured by type (earnings, dividend, AGM, etc.). The filter row above the calendar narrows by **event type**, **holding**, and **exchange**; the **Refresh ATHEX** button asks the ATHEX source for fresh data; **Import CSV** opens a drawer that accepts a hand-prepared CSV.

CSV columns (case-insensitive): `event_type`, `title`, `event_date` (YYYY-MM-DD or DD/MM/YYYY), at least one of `ticker` / `isin`. Optional: `exchange`, `event_time`, `timezone`, `description`, `url`, `status`, `external_id`. Rows match to holdings by ISIN first, then ticker, scoped to the active portfolio. Unmatched rows are still imported and tagged so an operator can audit them.

**ATHEX automation status — honest:** Athens Exchange does not currently publish a stable public machine-readable corporate-events feed. The Sources panel marks `athex-corporate-events` as *Unsupported* and `Refresh ATHEX` returns a customer-safe explanation. The Phase 9 release ships the table, API, UI, and CSV pipeline so the feature is usable today; once a reliable endpoint exists, only `src/corporate_events/athex.py` needs to be implemented.

## Insights overview (Phase 12)

The **Insights → Overview** sub-tab is a deterministic, evidence-backed roll-up of everything else in the app: News impact on your holdings, upcoming Corporate Events, Revenue geography coverage, Listing-country concentration, Alerts, Factor sensitivities, and Data gaps (e.g. "Revenue geography not uploaded yet", "AI narrator optional and not configured"). Every card carries:

* a severity badge (critical → info) derived from the source row;
* a category badge (news_impact / corporate_event / revenue_geography / listing_country / factor_sensitivity / alert / data_gap);
* an evidence chip list naming the rows the card came from (no claim without evidence);
* deep links to the surface that explains the card (News detail / Events tab / Exposures → Revenue geography / Alerts / Settings → Sources / Settings → AI Configuration);
* a "next step" line where one fits.

**Insights work without AI.** The deterministic generator is the only ground truth. The optional "AI narrate" toggle asks a configured AI provider to rewrite the wording of the top cards — it cannot add holdings, percentages, dates, or new claims. If the rewrite mentions a ticker outside the deterministic card's affected-holdings list, that rewrite is dropped and the deterministic original survives. If no AI provider is configured, the banner reports `AI requested but no provider configured — deterministic output shown` and the cards render unchanged.

The Coverage strip above the cards shows what inputs the generator had — holdings count, news (7d), corporate events (30d), active alerts, revenue-geography status, and AI provider state — so the operator can see the honest data-availability picture at a glance.

Revenue geography is **never inferred from listing country**. The "Listing country" exposure card and the "Revenue geography" insight card live separately, and a Phase 12 test enforces that no listing-country evidence ever ends up in a revenue-geography card.

## What this is NOT

- **Not** a cloud / multi-tenant service. Loopback-only, single user, single machine.
- **Not** a real-time market data terminal. News + events + portfolio analytics, on a 30-minute collection cycle.
- **Not** a substitute for a broker. No order routing.

See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for the full honest list.
