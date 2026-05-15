# Axion V1.0 — Known Limitations

This document describes features that are planned but not included in the V1.0 release,
along with operational constraints that operators should be aware of.

## Features Not Included in V1.0

### SEC EDGAR Integration
The SEC EDGAR source is declared in `config/sources.yaml` with `unsupported: true` because its
parser is not implemented in this build. Settings → News Sources shows it as **Unsupported** with
a disabled toggle. **Do not remove the `unsupported` flag** until a real `sec_edgar` parser ships.

### Subscription / paid news vendors
This build does **not** include Bloomberg, FactSet, Refinitiv, S&P Capital IQ, or other paid /
subscription data providers. Adding any of these requires OAuth or a vendor-specific client and
is tracked in `docs/OAUTH_ROADMAP.md`. Do not market or document Axion as having these integrations
before they ship.

### Finnhub Company News
Finnhub Market News is now wired up (Phase 7) and works with a `FINNHUB_KEY`. While the key is
missing, the source shows **Missing key** in Settings → News Sources — the customer can set the
env var, restart, and toggle the source on. Per-ticker Finnhub endpoints are not yet implemented;
the bundled source uses the general `/news` endpoint which works on the free tier.

### OAuth — not implemented
Axion does not yet ship any OAuth integration. There is no broker sync, no Google / Microsoft account linking, no paid-data-source authentication. Anthropic / OpenAI / Google Gemini AI providers use static API keys entered in **Settings → AI Configuration** and are the only credential type the customer has to manage today. See [`docs/OAUTH_ROADMAP.md`](docs/OAUTH_ROADMAP.md) for the design intent.

### Insights history + saved views — Phase 14
Insights → Overview now carries a *What changed* deck that reads the local `insight_snapshots` table (Phase 13) and shows how the deck has evolved over the last 7 / 30 / 90 days. **Deterministic-only**: no AI narration is consulted, no live prices are referenced, no investment-advice framing is added. The sparkline + summary chips are bucket counts; the row list pairs snapshot metadata with deep links to the surface that explains each card. Saved views now cover Insights Overview filters (category / severity / time window / AI toggle); existing saved views for News / Alerts / Portfolio / Operator continue to restore identically.

### Insights export + shareable Overview state — Phase 15
The Insights → Overview surface ships an **Export CSV** / **Export JSON** / **Copy share link** toolbar. The export endpoints (`POST /api/v1/intelligence/insights/export` and `GET /insights/export.json`) merge the current cards and the recent *What changed* history transitions for the same window into one self-describing payload. Filenames are `axion-insights-overview-YYYYMMDD-HHMMSS.{csv,json}`. The CSV's header row is fixed. The shareable hash carries the full Overview filter set (category / severity / AI toggle / history window / history state) via the existing Phase 9R encoder — no new surface was added, only filter keys. Privacy is structural: only customer-safe `InsightCard` / `InsightSnapshot` fields are ever emitted, a defensive `_safe_str` scrubber replaces anything that looks like a leaked secret or prompt body with `[redacted]`, and a per-request test gate scans every response for forbidden substrings (API keys, OAuth tokens, AI prompt bodies, uploaded PDF content, `.env` paths). The export is read-only — no snapshot writes, no AI re-narration, no live prices.

### Insight notifications — Phase 13
The Insights → Overview surface has an opt-in notification layer. Cards are fingerprinted on their deterministic content; new/escalated cards above the inbox severity floor surface in the Inbox sub-tab with read/unread state, and (if Telegram is configured) the high+ severity changes push a single message per change. Re-running with no material change does nothing — the fingerprint is identical. The persisted `insight_snapshots` table stores `card_key / category / severity / title / fingerprint / status` only — no AI prompt body, no narration text, no uploaded document content. A 15-minute scheduler job regenerates automatically; a Run-now button gives on-demand control. **No live prices**, **no investment advice** — Phase 13 only re-renders what Phase 12 already computed.

### Insights → Overview — Phase 12, deterministic-first
The **Insights → Overview** sub-tab is a deterministic, evidence-backed roll-up of News impact, Corporate Events, Alerts, Listing-country concentration, Revenue-geography coverage, Factor sensitivities, and data gaps. Every card carries its source rows as structured evidence. **Insights work without AI.** The optional AI narrator is grounded-only — it can rewrite wording but never adds new tickers, percentages, or claims; rewrites that mention untrusted tickers are dropped silently. With no AI provider configured, the page renders deterministic cards and the banner says so honestly.

The page is not a market terminal. It does not show live prices, does not generate buy/sell recommendations, and does not infer revenue geography from listing country.

### Revenue geography — Phase 10 foundation shipped, manual CSV only
The Portfolio → Exposures tab now has a separate **Revenue geography** card backed by the `revenue_geography` table, `/api/v1/exposures/revenue-geography`, and a CSV import drawer. The Phase 10 release ships:
* Migration v10 (`revenue_geography` with indexes on portfolio_id, holding_id, ticker, isin, fiscal_year, region, country) and a CHECK that rejects negative shares.
* A clean separation between **Listing country** (ISIN-prefix / venue derived) and **Revenue geography** (operator-uploaded). Listing country is **never** used as a fallback when revenue geography is missing.
* A manual CSV import path with ISIN-first matching, per-row errors, soft warnings when a company's allocations sum < 95% or > 105%, URL scrubbing of `apiKey=` / `token=` / `Bearer …` query params, and idempotent dedup on repeat uploads.
* Grounded AI context now carries a typed `holding_revenue_geography_status` (`missing` / `partial` / `available`) so prompts honestly say "revenue geography has not been uploaded" instead of guessing.

**Phase 11 ships a review-first AI extraction path** alongside the manual CSV. The *Import CSV* dialog on the Revenue geography card has a second tab, *AI extract from report*: operators upload a PDF annual report (or paste the relevant passage), Axion calls the configured AI vision provider with a strict anti-hallucination prompt, and the candidate rows appear in an editable review table. **Rows are never persisted without an explicit *Confirm* click.** Confirmed rows carry `source_type="ai_extracted"` so they're distinct from manual entries.

The AI extractor is **optional** — without an API key configured the tab reports a clean `missing_key` status and the manual CSV remains the supported path. The AI is told (and tested) **not** to infer revenue geography from headquarters, listing exchange, ISIN prefix, customer names, employee count, or country of incorporation. If the document has only narrative text with no explicit regional numbers, the AI returns an empty list and the UI says so honestly. PDF bytes are processed entirely in memory; nothing touches disk and the support bundle stores counts only, never document content.

### Corporate Events calendar — Phase 9 foundation shipped, ATHEX automation still degraded
The top-level **Events** tab now exists (separate from Insights → News) and is backed by the `corporate_events` table, `/api/v1/corporate-events`, and a monthly calendar UI. The Phase 9 release ships:
* Schema + migration `v9` (corporate_events with indexes on portfolio_id, holding_id, ticker, isin, event_date, event_type, exchange).
* Listing/ATHEX detection (ISIN prefix `GR`, venue alias, ticker `.AT` suffix).
* Manual CSV import path with ISIN-then-ticker matching, per-row validation, dedup, and URL scrubbing.
* `POST /api/v1/corporate-events/refresh` returns a typed honest status — `unsupported` in the default build.

**ATHEX automation is intentionally NOT enabled yet.** Athens Exchange does not currently publish a stable public machine-readable corporate-events feed; the Sources panel marks `athex-corporate-events` as **Unsupported** with a customer-safe note pointing at the CSV import. When a reliable upstream feed becomes available, only `src/corporate_events/athex.py` needs to be implemented — the schema, API, and UI are stable. Do not market or document Axion as having an automated ATHEX corporate-events feed until that work lands.

### Automated Price Data
There is **no `price_history` or `portfolio_snapshots` table** in this build — the
schema (v11) has no historical-price storage at all. Each holding carries only the
`current_price` value you imported; prices are set manually via CSV upload, the API,
or the dashboard, and are never refreshed automatically.

**There is no live market price feed.** Holdings rows carry only the `current_price` you imported. The dashboard does **not** call any market data vendor at runtime. Don't ship marketing copy that suggests otherwise.

### macOS `.app` is not code-signed
`Axion.app` bundles a working launcher but is **not signed or notarised**. macOS Gatekeeper will block first launch with "cannot be opened because Apple cannot check it for malicious software." To clear it once:

1. Right-click `Axion.app` → **Open**.
2. Click **Open** in the Gatekeeper dialog.

Or, recommended: use `./scripts/run_local.sh` from a terminal — that path avoids Gatekeeper entirely. Signing/notarisation is on the installer roadmap (`docs/RELEASE_CHECKLIST.md`).

### Event Clustering
Events are deduplicated (exact hash and near-duplicate detection), but related events are not
automatically grouped into clusters. The clustering infrastructure exists but is not active.
Planned for V2.

### OpenClaw Multi-Agent Chat
OpenClaw workspace configurations are included (`openclaw/` directory) but OpenClaw is an
external system that must be installed separately. The OpenClaw bridge API works, but
OpenClaw itself is not bundled with Axion.

### Default News Sources
V1 ships with **7 enabled RSS sources**: Federal Reserve, ECB, Google News Business,
WSJ Markets, MarketWatch, Seeking Alpha, and Investing.com — all keyless public feeds.
CNBC, Reuters, and Yahoo Finance feeds are present in `config/sources.yaml` but
**disabled**: CNBC returns HTTP 503 in many regions (geo-restriction), and the Reuters
and Yahoo Finance RSS endpoints are no longer active.
The optional `NEWSAPI_KEY` (NewsAPI) and `FINNHUB_KEY` (Finnhub) sources are also
disabled by default and require a key to enable.
Operators can add additional RSS or API sources in `config/sources.yaml`.

## Operational Constraints

### LLM Dependency (Optional but Recommended)
The `ANTHROPIC_API_KEY` is optional. Without it, Axion operates in **rule-based fallback mode**:
- **Classification** uses a built-in ticker lookup table (~100 common tickers). Less common
  tickers will get "Unknown" sector. ISIN-based geography still works.
- **Macro screening** (detecting indirect event impacts) is skipped entirely.
- **Impact analysis** uses keyword matching instead of LLM reasoning. Quality is significantly lower.
- **Digest generation** uses template-based summaries instead of LLM narratives.
- **Collection and risk monitoring** work identically with or without LLM.

For production use, an Anthropic API key is strongly recommended.

### Single-Process Architecture
Axion uses SQLite in WAL mode with a single Uvicorn worker. This is intentional for
simplicity and reliability. Do not increase `workers` above 1 in settings.yaml.

### LLM API Costs
Each news collection cycle may trigger LLM calls for macro screening (batches of 20 headlines).
Each event analysis creates one LLM call per event-holding pair. Costs depend on portfolio
size and news volume. Monitor usage via the Anthropic dashboard.

### Network Security
Axion binds to `127.0.0.1` by default (localhost only). To expose it on a LAN, change
`api.host` to `0.0.0.0` in `config/settings.yaml` and set `KLEITOS_API_KEY` for authentication.
HTTPS is not provided — use a reverse proxy (nginx, Caddy) if TLS is required.

### Backup
Database backups run automatically at 2:00 AM (configurable). Backups are stored in
`~/kleitos-data/backups/` with 7-day retention. For disaster recovery, also back up
the `config/` directory.

## Configuration Notes

### Risk Thresholds
Risk alert thresholds are configured in `config/settings.yaml` under the `risk:` section.
The file `config/risk_thresholds.yaml` is a reference document only — it is NOT loaded
by the application.

### Macro Screening
Macro screening settings are in `config/settings.yaml` under `macro_screening:`.
Set `enabled: false` to disable LLM-based indirect impact detection (reduces API costs).
