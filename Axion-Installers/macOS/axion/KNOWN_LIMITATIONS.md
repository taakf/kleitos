# Axion V1.0 — Known Limitations

This document describes features that are planned but not included in the V1.0 release,
along with operational constraints that operators should be aware of.

## Features Not Included in V1.0

### SEC EDGAR Integration
The SEC EDGAR source is defined in `config/sources.yaml` but the parser is not yet implemented.
The source is disabled by default. **Do not enable it** — it will not parse EDGAR responses correctly.
Planned for V2.

### Finnhub Company News
The Finnhub source is defined in `config/sources.yaml` but the parser is not yet implemented.
The source is disabled by default. Planned for V2.

### Automated Price Data
The `price_history` and `portfolio_snapshots` database tables exist but are not populated automatically.
Current prices must be set manually (via CSV upload, API, or dashboard). Planned for V2.

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
