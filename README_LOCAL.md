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

Three customer-facing terms that are easy to confuse — Axion uses them with these specific meanings:

| Term | What it means in the UI | Where you see it |
|------|-------------------------|------------------|
| **News** | Items collected from public news / regulatory / RSS / API sources (Fed press releases, market news, etc.). Each one is a record in the backend `events` table. | Dashboard → **Insights → News** sub-tab. |
| **Insights** | The analysis layer that consumes your portfolio + news + alerts + relationships. Includes News, Analysis, Digest, and Inbox sub-tabs. | Dashboard → top-level **Insights** tab. |
| **Corporate Events** | *Reserved for a future feature.* Will mean company-calendar items — earnings dates, dividends, general meetings, board announcements — fetched per-holding from sources like ATHEX. **Not implemented yet.** Do not confuse with News. |

The repo's backend keeps the table name `events` (matching the existing schema, API routes, and migrations); the customer-facing label for those rows is "News" / "news item". A future top-level **Events** tab will be added when corporate-calendar fetching exists.

## What this is NOT

- **Not** a cloud / multi-tenant service. Loopback-only, single user, single machine.
- **Not** a real-time market data terminal. News + events + portfolio analytics, on a 30-minute collection cycle.
- **Not** a substitute for a broker. No order routing.

See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for the full honest list.
