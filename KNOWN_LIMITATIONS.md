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

### Corporate Events calendar — not implemented yet
A future top-level **Events** tab is planned for company-calendar items (earnings dates, dividends, general meetings, board announcements, corporate actions). It will fetch per-holding from exchanges like ATHEX. **This feature does not exist yet.** The current "News" sub-tab under Insights shows news/regulatory items from RSS feeds — it does **not** show corporate calendar events. Do not market or document Axion as having a corporate events feed until the integration ships.

### Automated Price Data
The `price_history` and `portfolio_snapshots` database tables exist but are not populated automatically.
Current prices must be set manually (via CSV upload, API, or dashboard). Planned for V2.

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
V1 ships with 6 enabled RSS sources: Federal Reserve, ECB, MarketWatch, Seeking Alpha,
Investing.com, and CNBC. CNBC may return HTTP 503 in some regions (geo-restriction).
Reuters and Yahoo Finance feeds are disabled because their RSS endpoints are no longer active.
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
