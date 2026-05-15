# Axion — Beta Test Script

A step-by-step procedure for a beta tester. Each step lists the action
and the **expected result**. Pair it with
[`CUSTOMER_ACCEPTANCE_CHECKLIST.md`](CUSTOMER_ACCEPTANCE_CHECKLIST.md) —
this script tells you *what to do*; the checklist records *whether it
passed*.

- Build: Axion `1.0.0` (`local` channel), schema `v11`.
- Time: ~20–30 minutes for a full pass.
- You need: the release zip, Python 3.11+, and (optionally) an AI
  provider API key and a portfolio CSV.

---

## Step 1 — Install

1. Unzip `axion-macos.zip` (or `axion-windows.zip`) into a folder you
   own (e.g. `~/Axion`).
2. Open `RELEASE_MANIFEST.json`.
   - *Expected:* it shows `"app_version": "1.0.0"`,
     `"release_channel": "local"`, a `git_commit`, and a `guarantees`
     list (no DB files, no API keys).

## Step 2 — First launch

1. **macOS/Linux:** in a terminal, `cd` into the folder and run
   `./scripts/run_local.sh`.
   **Windows:** double-click `Axion.bat`.
   *(macOS Finder option: double-click `Axion.app`; on first launch
   right-click → **Open** to clear Gatekeeper — the app is unsigned.)*
2. Wait for the staged setup (venv, dependencies, migrations) to finish.
   - *Expected:* the launcher ends by reporting the app is running and
     opens `http://127.0.0.1:7777/dashboard/`.
3. Look at the dashboard on the fresh database.
   - *Expected:* honest empty states — "no holdings yet", "no news
     yet" — and **no fabricated data**. Six tabs: Portfolio, Insights,
     Events, Alerts, Assistant, Settings.

## Step 3 — Import the sample portfolio

1. Portfolio → Holdings → **Upload**; choose the bundled
   `sample_portfolio.csv`.
   - *Expected:* holdings import; the Holdings table fills with rows
     and P&L columns.

## Step 4 — Import a real portfolio

1. Repeat the Upload flow with your own holdings CSV
   (columns: ticker, quantity, and optionally cost/price/currency).
   - *Expected:* rows import; any per-row problems are listed without
     aborting the whole batch.

## Step 5 — Configure an AI provider (optional)

1. Settings → AI Configuration → paste an Anthropic / OpenAI / Gemini
   key → **Test**.
   - *Expected:* a typed status (Active / Invalid key / Quota /
     Unreachable / …). The key is never echoed back.
2. If you skip this step: confirm the rest of the app still works.
   - *Expected:* deterministic mode runs normally; it is not an error
     state.

## Step 6 — Configure source keys (optional)

1. Settings → News Sources.
   - *Expected:* the 7 bundled RSS feeds are listed and need no key.
2. (Optional) add a `NEWSAPI_KEY`.
   - *Expected:* a source without its key shows **Missing key** — a
     typed status, not a crash.

## Step 7 — Collect & inspect News

1. News is collected from the public RSS feeds on a schedule. Give it a
   collection cycle, then open **Insights → News**.
   - *Expected:* news items appear; the filter bar (search, source,
     type, factor, time window) narrows the list; **Reset** clears it.
2. Open a news item.
   - *Expected:* a detail modal with factor tags / affected holdings /
     causal chain — all deterministic rule outputs.

## Step 8 — Inspect the Events calendar

1. Open the top-level **Events** tab.
   - *Expected:* a monthly corporate-events calendar — a *separate*
     surface from News. Empty until you import corporate events.
2. Use the **Import CSV** drawer to load a corporate-events CSV.
   - *Expected:* events appear on the calendar; the import reports
     matched/unmatched counts.

## Step 9 — Inspect Exposures

1. Portfolio → **Exposures**.
   - *Expected:* a **Listing country** card (derived automatically from
     ISIN/venue) and a **separate Revenue geography** card. Revenue
     geography starts empty — Axion never infers it.

## Step 10 — Upload revenue geography

1. In the Revenue geography card click **Import CSV**; load a CSV with
   `region`, `revenue_share`, and `ticker` or `isin`.
   - *Expected:* rows match by ISIN then ticker; the chart updates; any
     unallocated share goes to an honest "Other / unallocated" bucket.

## Step 11 — Run & export Insights

1. Insights → **Overview** → **Run now**.
   - *Expected:* deterministic, evidence-backed cards render, each with
     a severity, a category, and a deep link.
2. Click **Export CSV**, then **Export JSON**.
   - *Expected:* files download named
     `axion-insights-overview-YYYYMMDD-HHMMSS.{csv,json}`.
3. Click **Copy share link**, open the copied URL in a new tab.
   - *Expected:* the Overview restores with the same filters.

## Step 12 — Generate a support bundle

1. Run `python scripts/support_bundle.py`.
   - *Expected:* a redacted zip is written to
     `~/axion-data/support/`. Opening it: diagnostics + redacted
     env/settings + log tails; **no** database, **no** raw `.env`,
     **no** API keys.

## Step 13 — Reset demo data

1. Follow [`DEMO_RESET.md`](DEMO_RESET.md).
   - *Expected:* the app returns to a clean state; the install itself
     is intact.

## Step 14 — Uninstall / cleanup

1. Stop the app. Delete the unzipped Axion folder, `~/axion-data/`, and
   `~/.axion.env`.
   - *Expected:* the app and all its data are gone. *(Legacy
     `/Applications` install: run `./scripts/uninstall-mac.sh`.)*

---

## What to report

For each step, record pass/fail in
[`CUSTOMER_ACCEPTANCE_CHECKLIST.md`](CUSTOMER_ACCEPTANCE_CHECKLIST.md).
If anything fails, attach the support bundle from Step 12 — it carries
the diagnostics needed to investigate, with secrets already redacted.

## Known limitations to expect (not bugs)

- The macOS `.app` is **unsigned** — first launch needs right-click →
  **Open**, or use the terminal launcher.
- There is **no live market-price feed** — `current_price` is whatever
  you imported.
- **No broker sync, no OAuth** — OAuth is roadmap-only.
- **SEC EDGAR** and **ATHEX** automated feeds are **unsupported**;
  corporate events use the CSV-import path.
- Insights are **operational signals, not investment advice**.
