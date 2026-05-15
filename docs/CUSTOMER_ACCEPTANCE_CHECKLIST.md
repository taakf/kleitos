# Axion — Customer Acceptance Checklist

A structured acceptance pass for the Axion local beta. Work top to
bottom, tick each item, and record pass/fail. For an exact
step-by-step procedure with expected output, follow
[`BETA_TEST_SCRIPT.md`](BETA_TEST_SCRIPT.md) alongside this checklist.

- **Build under test:** Axion `1.0.0`, release channel `local`,
  database schema `v11`.
- **Verification status of this build:** see the bottom of this file.

---

## 1. Install

- [ ] The release zip (`axion-macos.zip` / `axion-windows.zip`) unzips
      cleanly to a folder you own.
- [ ] `RELEASE_MANIFEST.json` is present and states `app_version`,
      `release_channel: local`, a git commit, and the no-database /
      no-API-keys guarantees.
- [ ] No `.env`, `*.db`, virtualenv, or `.git` folder is inside the zip.

## 2. First launch

- [ ] macOS/Linux: `./scripts/run_local.sh` completes its staged
      output and ends with the dashboard URL. *(Or double-click
      `Axion.app` — first launch needs right-click → **Open** once, as
      it is unsigned.)*
- [ ] Windows: double-clicking `Axion.bat` (or running
      `run_local.ps1`) sets up and starts the app.
- [ ] The dashboard opens at `http://127.0.0.1:7777/dashboard/`.
- [ ] On a fresh database the dashboard shows an honest empty state
      (no fabricated holdings, news, or events).
- [ ] The six top tabs render: **Portfolio · Insights · Events ·
      Alerts · Assistant · Settings**.

## 3. Import a portfolio

- [ ] **Sample CSV** — Portfolio → Holdings → Upload with the bundled
      `sample_portfolio.csv` imports without error.
- [ ] **Real CSV** — the same flow with your own holdings CSV imports;
      per-row errors (if any) are reported without aborting the batch.
- [ ] Imported holdings appear in the Holdings table with P&L columns.

## 4. Optional AI provider (skippable)

- [ ] Settings → AI Configuration accepts an Anthropic / OpenAI /
      Gemini key; the **Test** button reports a typed status.
- [ ] With **no** AI key, the app still works fully — deterministic
      mode is presented as a normal mode, not an error.

## 5. Optional source keys (skippable)

- [ ] The 7 bundled RSS feeds need no key.
- [ ] Settings → News Sources accepts an optional `NEWSAPI_KEY` /
      `FINNHUB_KEY`; a source without its key shows the typed
      **Missing key** status (not a crash).

## 6. Collect & inspect

- [ ] News is collected from the public RSS feeds on the schedule;
      **Insights → News** populates and its filters (search, source,
      type, factor, time window) narrow the list.
- [ ] **Events** (top tab) shows the corporate-events calendar —
      separate from News; the CSV-import drawer accepts a corporate
      events CSV.
- [ ] **Portfolio → Exposures** shows **Listing country** (auto, from
      ISIN/venue) and a separate **Revenue geography** card.

## 7. Revenue geography upload

- [ ] Portfolio → Exposures → Revenue geography → Import CSV accepts a
      `region,revenue_share` CSV; rows match to holdings by ISIN then
      ticker; the chart updates and unallocated share is shown
      honestly.

## 8. Insights

- [ ] **Insights → Overview** renders deterministic, evidence-backed
      cards; **Run now** regenerates them.
- [ ] **Export CSV** and **Export JSON** download a file named
      `axion-insights-overview-YYYYMMDD-HHMMSS.{csv,json}`.
- [ ] **Copy share link** copies a URL that restores the same Overview
      filters when opened.

## 9. Support bundle

- [ ] `python scripts/support_bundle.py` writes a redacted zip to
      `~/axion-data/support/`.
- [ ] The bundle contains diagnostics + redacted env/settings + log
      tails, and **excludes** the database, raw `.env`, and API keys.

## 10. Reset demo data

- [ ] Following [`DEMO_RESET.md`](DEMO_RESET.md) returns the app to a
      clean state without corrupting the install.

## 11. Uninstall / cleanup

- [ ] Deleting the unzipped Axion folder, `~/axion-data/`, and
      `~/.axion.env` fully removes the app and its data. *(Legacy
      `/Applications` install: `./scripts/uninstall-mac.sh`.)*

---

## Accessibility / keyboard spot-check

- [ ] Keyboard `Tab` reaches the major controls; the focus ring is
      visible.
- [ ] Dialogs open and close (Escape works); focus returns to the
      control that opened them.
- [ ] No browser console errors during a normal walkthrough.

## Honesty checks

- [ ] No surface claims live market prices, broker sync, OAuth login,
      or bundled paid-vendor data.
- [ ] Unsupported items (SEC EDGAR automation, ATHEX automated feed)
      are clearly labelled unsupported.
- [ ] Insights are framed as operational signals — not investment
      advice.

---

## Build verification status

> **This build is LOCAL-VERIFIED.** Every gate above and the full
> non-e2e test suite pass on a local developer machine. The
> cross-platform CI workflow (`.github/workflows/release-local-app.yml`)
> validates macOS + Windows but **only runs on a pull request** — it
> has not run for this branch yet. Do **not** describe this package as
> "CI-verified" until the CI run on the PR is green.
