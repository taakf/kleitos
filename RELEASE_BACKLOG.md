# Axion Release Backlog

## Status Key
- **OPEN** — Not started
- **IN PROGRESS** — Currently being worked
- **DONE** — Completed and validated
- **DEFERRED** — Not required for V1, tracked for later
- **DESCOPED** — Removed from V1 scope with documentation

---

## MUST-FIX for First-Client Release

### P0 — Critical (Blocks delivery)

| # | Issue | Status | Notes |
|---|-------|--------|-------|
| 1 | .env not loaded from project dir — config bug | **DONE** | Config now loads project-root .env as fallback |
| 2 | macro_screening YAML keys silently ignored | **DONE** | Added MacroScreeningSettings Pydantic model |
| 3 | SEC EDGAR source enabled with missing parser | **DONE** | Disabled in sources.yaml, documented as V2 |
| 4 | Auth disabled by default — insecure | **DONE** | Default changed to auth_enabled: true |
| 5 | Default host 0.0.0.0 — exposes to network | **DONE** | Changed to 127.0.0.1 in both config.py and settings.yaml |
| 6 | risk_thresholds.yaml orphaned — misleading | **DONE** | Added REFERENCE ONLY header explaining it's not loaded |
| 7 | 1 test failure (test_classify_then_match) | **DONE** | Fixed test to include ticker in event text |
| 8 | Conflicting install docs | **DONE** | Added cross-reference header to docs/INSTALL.md |

### P1 — High (Reduces client confidence)

| # | Issue | Status | Notes |
|---|-------|--------|-------|
| 9 | Finnhub parser missing | **DONE** | Already disabled in sources.yaml; documented in KNOWN_LIMITATIONS.md |
| 10 | No API smoke tests | **DONE** | Added 21 API smoke tests covering all endpoint groups |
| 11 | No scheduler job timeouts | OPEN | Jobs could hang indefinitely |
| 12 | Telegram create_task without outer error handler | OPEN | Low risk — internal try/except exists |
| 13 | Sources not synced to DB from YAML on startup | OPEN | sources table stays empty |

### P2 — Medium (Polish for professional delivery)

| # | Issue | Status | Notes |
|---|-------|--------|-------|
| 14 | Dedup cluster_id feature is no-op | **DESCOPED** | Documented in KNOWN_LIMITATIONS.md as V2 |
| 15 | PriceHistory/PortfolioSnapshot unused | **DESCOPED** | Documented in KNOWN_LIMITATIONS.md as V2 |
| 16 | No sample/demo portfolio for onboarding | OPEN | |
| 17 | .env.template needs update | **DONE** | Updated with secure defaults and guidance |

---

## DEFERRED to Post-V1

| # | Feature | Reason |
|---|---------|--------|
| D1 | SEC EDGAR parser implementation | Complex, not critical for V1 RSS-based collection |
| D2 | Finnhub parser implementation | Requires API key, not critical for V1 |
| D3 | Price data feed integration | PriceHistory schema ready, no data source yet |
| D4 | Portfolio snapshot automation | Schema ready, no trigger logic |
| D5 | Event clustering (dedup.mark_cluster) | Dedup works fine without clustering |
| D6 | Multi-language news support | English-only for V1 |
| D7 | OpenClaw verification | Optional integration, cannot verify without OpenClaw binary |

---

## RESOLVED

| # | Issue | Resolution | Cycle |
|---|-------|------------|-------|
| 1 | .env not loaded from project dir | Added project-root .env fallback in config.py | Cycle 1 |
| 2 | macro_screening not in Pydantic | Added MacroScreeningSettings model | Cycle 1 |
| 3 | SEC EDGAR source enabled | Set enabled: false in sources.yaml | Cycle 1 |
| 4 | Auth disabled by default | Changed default to true | Cycle 1 |
| 5 | 0.0.0.0 host default | Changed to 127.0.0.1 | Cycle 1 |
| 6 | Orphaned risk_thresholds.yaml | Added REFERENCE ONLY header | Cycle 1 |
| 7 | Failing test | Fixed event text in test | Cycle 1 |
| 8 | Conflicting install docs | Added cross-reference to docs/INSTALL.md | Cycle 2 |
| 9 | Finnhub parser documented | Documented as V2 in KNOWN_LIMITATIONS.md | Cycle 2 |
| 10 | No API tests | Added 21 smoke tests | Cycle 2 |
| 14 | Dedup clustering no-op | Documented as V2 | Cycle 2 |
| 15 | Unused tables | Documented as V2 | Cycle 2 |
| 17 | .env.template outdated | Updated with secure defaults | Cycle 2 |
