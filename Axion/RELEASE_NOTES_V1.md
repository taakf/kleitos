# Axion by 4Labs V1.0 — Release Notes

**Release date:** March 2026
**Release type:** First client delivery
**Release decision:** CONDITIONAL GO

## What Axion Does

Axion is a locally-deployed portfolio intelligence system. It monitors financial news
sources, matches events to your holdings, scores their impact, tracks concentration risks,
and generates daily intelligence digests.

It runs 24/7 on your machine with no cloud dependency (except optional LLM calls).

## V1.0 Feature Set

### Core Intelligence Pipeline (Proven)
- Portfolio ingestion via CSV upload or manual entry
- Automatic security classification (sector, geography, themes)
- News collection from 6 RSS sources every 30 minutes
- Event-to-holding matching by ticker symbol and company name
- Rule-based impact scoring (direction, magnitude, materiality)
- 9 quantitative risk checks (concentration, correlation, drift)
- Coverage quality monitoring per holding
- Daily digest generation at 07:00
- Full audit trail of every system action

### Dashboard (Proven)
- Real-time portfolio overview with system status
- Holdings management with search, sort, and CRUD
- Exposure breakdowns by sector, geography, currency, and theme
- Event feed with type and materiality classification
- Alert management with severity and acknowledge workflow
- Analysis notes with confidence scoring
- System health and agent activity monitoring
- Dark theme, responsive design

### Optional Enhancements (Available but Unverified in V1.0)
- **LLM-enhanced analysis** via Anthropic Claude API — richer classification, indirect
  impact detection, executive-quality digests. Requires `ANTHROPIC_API_KEY`.
  *Code is complete and statically audited. Not live-tested in this release cycle.*
- **Telegram bot** — 15+ commands, CSV upload, AI chat. Requires bot token.
- **Email notifications** — digest and alert delivery. Requires SMTP configuration.

## Default News Sources

| Source | Type | Coverage |
|--------|------|----------|
| Federal Reserve | RSS | US monetary policy, regulatory |
| ECB | RSS | European monetary policy |
| MarketWatch | RSS | US/global market news |
| Seeking Alpha | RSS | Company news, analyst takes |
| Investing.com | RSS | Global financial news |
| CNBC | RSS | Market news (may be geo-restricted) |

Additional sources can be added in `config/sources.yaml`.

## Analysis Modes

| Mode | When Active | Capability |
|------|-------------|------------|
| **Rule-based** | No API key configured | Ticker/name matching, keyword scoring, 100+ ticker classification lookup |
| **AI-enhanced** | `ANTHROPIC_API_KEY` set | Claude-powered classification, macro screening, impact analysis, narrative digests |

The system is fully functional in rule-based mode. AI-enhanced mode provides materially
richer analysis but has not been live-tested in this release.

## Known Limitations

See `KNOWN_LIMITATIONS.md` for the complete list. Key items:

- SEC EDGAR and Finnhub parsers not implemented (disabled)
- No automated price data feeds (manual CSV/API input only)
- Event clustering not implemented (dedup works without it)
- Single-process SQLite architecture (by design — single-user)
- No HTTPS (use reverse proxy if TLS needed)

## Supported Deployment

| Platform | Method | Status |
|----------|--------|--------|
| **Windows** | `Axion.bat` double-click launcher | Tested, proven |
| **Windows** | Manual `pip install` + `uvicorn` | Tested, proven |
| **macOS** | `scripts/install-mac.sh` | Available, not tested this cycle |
| **Docker** | `docker compose up --build` | Available, not tested this cycle |

## Configuration Required

**Minimum:** No configuration needed. System runs with defaults.

**Recommended:** Copy `.env.template` to `.env` and optionally set:
- `ANTHROPIC_API_KEY` — enables AI-enhanced analysis
- `KLEITOS_API_KEY` — required if exposing on LAN

See `.env.template` for all options with explanations.

## Quality Evidence

- 75 automated tests (unit + integration + smoke) — all passing
- 28 API endpoint smoke tests — all passing
- End-to-end pipeline verified with real RSS data (62 events from 5 sources)
- Dashboard visually verified across all 9 tabs
- Clean-install path verified from fresh state
- Documentation verified against live API
- Zero JavaScript console errors
- Zero server errors (except expected CNBC 503 on some networks)

## Release Hardening Summary

10 cycles of systematic verification and improvement:
- 3 pipeline-blocking bugs found and fixed
- 17 documentation URL errors corrected
- 4 new RSS sources added for broader coverage
- Company name matching added to event linking
- Security name lookup added to classification
- Dashboard redesigned with premium overview band
- All labels humanized from snake_case to Title Case
- System status bar shows LLM mode and source health
- Alert ticker badges resolved from UUIDs to symbols
- Sidebar removed for cleaner full-width layout
- Favicon added
