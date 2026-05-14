# Axion — Customer quick start

A 5-minute walkthrough from first launch to "I'm using it."

## 1. Install Python (one time)

Axion needs **Python 3.11 or newer**.

- **macOS:** Install via Homebrew (`brew install python@3.12`) or from <https://www.python.org/downloads/>.
- **Windows:** Download from <https://www.python.org/downloads/> and **tick "Add Python to PATH"** during install.

Check it works:

```bash
python3 --version    # macOS / Linux
python --version     # Windows
```

You should see `Python 3.11.x` or higher.

## 2. Launch Axion

**macOS / Linux:**
```bash
./scripts/run_local.sh
```

**Windows:**
```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\run_local.ps1
```

Or simply **double-click `Axion.bat`** on Windows.

The first run takes 1–2 minutes (venv + dependencies). Subsequent runs are fast.

When you see:

```
============================================
  Axion is running.
============================================
  Dashboard : http://127.0.0.1:7777
```

…the dashboard will open in your browser automatically (macOS / Windows). If not, open <http://127.0.0.1:7777/dashboard/> manually.

## 3. Import a portfolio

In the dashboard:

1. Click **Portfolio** → **Holdings**.
2. Drag in your CSV, or click **Upload** and pick `sample_portfolio.csv` from the project root to try the bundled demo data.
3. Review the extracted rows. Adjust any field if needed.
4. Click **Import**.

Your holdings appear in the table within a second.

### Supported CSV format

The simplest format is:

```
ticker,quantity,price,currency
AAPL,150,178.50,USD
MSFT,80,425.00,USD
NESN,40,98.50,CHF
```

Optional columns: `isin`, `name`, `avg_cost_basis`, `market_value`, `weight_pct`.

Tickers must be 1–10 characters (letters, digits, and `.`). Currency must be a 3-letter ISO code. Quantity must be > 0.

For PDFs and scanned documents, see "AI features" below.

## 4. Explore the dashboard

| Tab | Shows |
|-----|-------|
| **Portfolio** | Holdings, exposures (sector / listing-country / currency), trade history. The "Geography" chart reflects **listing country**, not revenue geography (a dedicated revenue-geography phase is planned). |
| **Insights** | News collected from RSS / API sources, ranked by impact on your holdings, plus deterministic analysis and digests. Sub-tabs: News, Analysis, Digest, Inbox. |
| **Events** | Scheduled corporate / issuer events on a monthly calendar (earnings, dividends, AGMs, …). Phase 9 ships ATHEX-first; data is loaded via the **Import CSV** drawer until a stable upstream feed is available. |
| **Alerts** | Concentration breaches, calendar clusters, stale data warnings |
| **Assistant** | Conversational queries (requires AI provider — disabled otherwise) |
| **Settings** | API keys, provider selection, source health |

### Insights → Overview

The first sub-tab under **Insights** is the Phase 12 Overview. It surfaces a ranked, evidence-backed list of insight cards drawn from News impact, upcoming Corporate Events, Revenue geography coverage, Listing-country concentration, Alerts, and Factor sensitivities. Every card shows its severity, category, the rows it came from, and a deep link to the surface that explains it. **No AI is required** — the AI narrate toggle is optional and never adds new facts.

**Phase 13** adds a quiet notification layer on top:

* A `New` / `Escalated` / `Already notified` pill on each card based on a deterministic fingerprint vs the last snapshot.
* A scheduled job regenerates insights every 15 minutes; the **Run now** button forces a pass on demand.
* `new`/`escalated` cards above medium severity appear in the **Inbox** sub-tab with read/unread state.
* When Telegram is configured, `high+` severity cards push a single message per change. Without Telegram, the dispatcher is silent — Insights still works end-to-end.

**Phase 14** adds a *What changed* panel above the card grid:

* 7d / 30d / 90d windows.
* Sparkline of new/escalated/unchanged transitions per day, built only from local snapshots.
* List of recent transitions with deep links to the surface that explains each card.
* **Save current view** now pins your Insights Overview filters (category, severity, time window, AI toggle) so you can return to the same slice with one click.

### Reading the Exposures cards

The Portfolio → Exposures tab now shows **Listing country** (instrument-listing exposure derived from ISIN/venue) and a separate **Revenue geography** card. Revenue geography is populated only when you upload a CSV via the card's *Import CSV* button — Axion never infers where a company earns money from where its shares are listed. CSV columns: `region`, `revenue_share`, plus at least one of `ticker` / `isin`. `revenue_share` accepts `0.45`, `45`, or `45%`. Holdings you haven't uploaded for show up in a "Holdings without revenue breakdowns" panel so the gap is visible at a glance.

### Optional — extract from a PDF annual report (review-first)

The same *Import CSV* dialog has a second tab: **AI extract from report**. Drop in an annual report PDF (or paste a regional-revenue passage as text), pick *Extract candidates*, and review the editable rows the AI proposes. Confidence and evidence quotes are shown per row. **Nothing is saved until you click *Confirm***. Without an AI key configured the tab reports `missing_key`; manual CSV always works.

### Filtering the News tab

Above the table you'll see a filter bar with **Search**, **Source**, **Type**, **Factor**, **Materiality**, **24h / 7d / 30d / All** range pills, and a **Linked holdings only** toggle. Search is debounced and queries the backend; the other controls take effect immediately. The **Reset** button clears every filter. Each row carries small chips: **Linked** when the story matched a holding, **Macro signal** when the deterministic factor classifier tagged it. Click any row to open the news-item modal with the why-it-matters narrative, affected holdings, causal chains, and any related analyses or alerts.

## 5. (Optional) Configure an AI provider

The core platform runs without AI. If you want LLM-enhanced impact scoring, narrative digests, AI vision PDF extraction, and the conversational Assistant tab:

1. Get an API key from one of the supported providers:
   - **Anthropic (Claude)** — `ANTHROPIC_API_KEY` (`sk-ant-…`)
   - **OpenAI / ChatGPT** — `OPENAI_API_KEY` (`sk-…` or `sk-proj-…`)
   - **Google Gemini** — `GOOGLE_API_KEY` (`AIza…`)
2. In the dashboard, go to **Settings → AI Configuration**.
3. Pick the provider as **Primary**, paste the key, click **Save Key**, then **Save Provider Selection**.
4. Click **Test** next to the key field. The button sends one minimal request and reports the result. Possible statuses: **Active**, **Not configured**, **Invalid key**, **Quota / rate-limit**, **Unreachable**, **Misconfigured**, **Error**.
5. Restart Axion (Ctrl+C in the launcher window, then re-run the launcher) for the new key to take effect.

You can optionally add a **Backup** provider — if the primary returns a rate-limit / auth / 5xx error, Axion automatically retries with the backup.

Keys are stored at `~/.axion.env` with `600` permissions on your machine. They are never sent anywhere except to the chosen provider you configured. The support bundle (`scripts/support_bundle.py`) redacts key-shaped strings.

> **OAuth is not yet supported.** Axion does not connect to brokers, Google / Microsoft accounts, or any OAuth-authenticated source. See [OAUTH_ROADMAP.md](OAUTH_ROADMAP.md) for the future plan.

## 6. (Optional) Add news sources

Axion ships with a curated allowlist in `config/sources.yaml`. Seven public RSS feeds are enabled by default and work immediately (Federal Reserve, ECB, MarketWatch, Google News Business, WSJ Markets, Seeking Alpha, Investing.com).

Go to **Settings → News Sources** to see live status for each source. The **Status** column is a typed label — *Active*, *Disabled*, *Missing key*, *Degraded*, *Rate limited*, *Unreachable*, *Parser error*, *Unsupported*, *Misconfigured*, or *Error* — and the **Auth env var** column names the variable to set for any key-required source.

### Optional API-key sources

| Source | Env var | Where to get it |
|--------|---------|-----------------|
| NewsAPI Business | `NEWSAPI_KEY` | <https://newsapi.org/> (free dev tier) |
| Finnhub Market News | `FINNHUB_KEY` | <https://finnhub.io/> (free tier) |

Set the key in `~/.axion.env`, restart Axion, then toggle the source on. While the key is missing, the source shows **Missing key** in the table — that's the expected state, not an error.

Paid / subscription sources (Bloomberg, FactSet, Refinitiv, S&P Capital IQ) are **not** bundled — see `docs/OAUTH_ROADMAP.md`.

ATHEX corporate-events calendar is **not** part of this build — `KNOWN_LIMITATIONS.md` has the future plan.

## 7. Stopping and restarting

- **Stop:** Press **Ctrl+C** in the launcher window. The server shuts down cleanly.
- **Restart:** Re-run the launcher. Your data and settings persist in `~/axion-data/` and `~/.axion.env`.

## 8. Verifying the install

If anything looks off, run the bundled smoke test:

```bash
.venv/bin/python scripts/smoke_local.py   # macOS / Linux
.venv\Scripts\python.exe scripts\smoke_local.py   # Windows
```

It runs 16 end-to-end checks against a throwaway temp DB (your real data is untouched) and prints PASS / FAIL for each.

## 9. Sending Axion's state to support

If something doesn't work and you'd like to share state with us, run:

```bash
.venv/bin/python scripts/support_bundle.py        # macOS / Linux
.venv\Scripts\python.exe scripts\support_bundle.py  # Windows
```

This writes a redacted zip to `~/axion-data/support/`. Attach that single file — it carries the app version, OS info, schema version, table counts, source health summary, and the last 200 KB of each log file. Secrets are removed by both env-var name and value pattern. The zip never contains your database, backup files, raw `.env`, or holdings.

## 10. Port already in use?

If the launcher reports `Port 7777 is in use by another application`, it also tries to show the process name and PID. Two options:

- **Close the other application** (often a stale Axion from a previous launch).
- **Run Axion on a different port:**
  ```bash
  AXION_PORT=7778 ./scripts/run_local.sh                # macOS / Linux
  ```
  ```powershell
  $env:AXION_PORT='7778'; PowerShell -ExecutionPolicy Bypass -File scripts\run_local.ps1   # Windows
  ```

The dashboard URL will be `http://127.0.0.1:7778/dashboard/` in that case.

## Where your data lives

| Item | Location |
|------|----------|
| Database, logs, exports | `~/axion-data/` (or `~/kleitos-data/` on older installs) |
| API keys and settings | `~/.axion.env` |
| Source allowlist | `config/sources.yaml` (in the project folder) |
| Pre-upgrade safety backups | `~/axion-data/backups/kleitos-pre-v<N>-<timestamp>.db` |

### What happens during an upgrade

When you launch a new build of Axion against an older database, the launcher:

1. Verifies the database is readable and not corrupt.
2. Creates a consistent backup at `~/axion-data/backups/kleitos-pre-v<N>-<timestamp>.db` (where `<N>` is the new schema version).
3. Only then applies the migration steps.

If the backup write fails, the launcher refuses to migrate and tells you what to fix. **Your live database is never modified.**

### If you see one of these recovery messages

- **"Your Axion data was created by a newer version of Axion"** — this build's schema is older than your data. Either update Axion, or restore an older backup from `~/axion-data/backups/`. Your data is intact.
- **"Axion could not open the database"** — the DB file is corrupt or unreadable. Axion does **not** delete or overwrite it. Restore a backup, or move the file aside and relaunch for a fresh DB.
- **"Pre-migration backup failed"** — free disk space or fix folder permissions on `~/axion-data/backups/`, then relaunch. No schema change was applied.

To start completely fresh, see [DEMO_RESET.md](DEMO_RESET.md).

## Need help?

- Architecture: [../ARCHITECTURE.md](../ARCHITECTURE.md)
- Honest limitations: [../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md)
- Troubleshooting: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Operator runbook: [../OPERATOR_CHECKLIST.md](../OPERATOR_CHECKLIST.md)
