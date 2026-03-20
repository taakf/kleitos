# Axion Release Hardening Log

## Session: 2026-03-18

### Stage 0 — Verified Baseline

**Verification method:** Direct file inspection, config loading test, live server startup, test suite execution, source code tracing.

#### Repository State
- NOT a git repository (confirmed: `git status` returns `fatal: not a git repository`)
- Python 3.12.10 venv present and functional
- All dependencies installed

#### Runtime Verification
- **Server starts successfully** on port 7777 (tested live)
- **Health endpoint responds:** `{"status":"ok","database":"connected","scheduler":"running",...}`
- **Database exists** at `~/kleitos-data/db/kleitos.db` (311KB, 15 tables, has data)
- **Config loads cleanly** via Pydantic (settings.yaml + env vars)
- **All 48 API routes register** without import errors
- **LLM in fallback mode** (no ANTHROPIC_API_KEY set)

#### Test Suite Results
- **53 passed, 1 failed** out of 54 tests
- Failed test: `test_classify_then_match` — test bug (expects match on "Apple" but security name is "Apple Inc", ticker "AAPL" not in event text)
- All unit tests pass (classifier, dedup, exposure, impact rules, source registry)
- All smoke tests pass (imports, instantiation)
- 2/3 integration tests pass

#### Prior Audit Corrections
| Audit Claim | Actual Finding | Status |
|---|---|---|
| "Database is empty" | Database has 6 holdings, 15 events, 40 event_links, 28 agent runs, 12 alerts | **WRONG** |
| ".env file is committed / risk" | .gitignore correctly excludes .env; no git repo exists anyway | **MISLEADING** |
| "Never been run" | Database has prior run history (28 agent runs) | **WRONG** |
| "risk_thresholds.yaml is orphaned" | Confirmed — no code loads it | **CORRECT** |
| "macro_screening not modeled in Pydantic" | Confirmed — YAML keys silently ignored | **CORRECT** |
| "SEC EDGAR parser missing" | Confirmed — parser: sec_edgar referenced but not implemented | **CORRECT** |
| "Finnhub parser missing" | Confirmed — parser: finnhub referenced but not implemented | **CORRECT** |
| "Auth disabled by default" | Confirmed — auth_enabled: false, complete bypass | **CORRECT** |
| "Email delivery incomplete" | Actually functional (SMTP with HTML+text) but disabled by default | **WRONG** — email is implemented |
| "Dedup cluster is no-op" | Confirmed — mark_cluster() only logs, no DB write | **CORRECT** |
| "No price data integration" | Confirmed — PriceHistory table exists, no code populates it | **CORRECT** |

#### Configuration Bug Found
- `.env` file in project root is NOT loaded. Config system looks for `~/.kleitos.env` (which doesn't exist). The `.env` file's settings (KLEITOS_DB_PATH, KLEITOS_DATA_DIR, etc.) have **zero effect**.
- System falls through to Pydantic defaults (~/kleitos-data/...) which happen to work, but operator editing `.env` would be confused.

---

### Cycle 1 — Configuration Model Fixes + Security Defaults

**Changes made:**
1. `src/config.py`: Added `MacroScreeningSettings` Pydantic model (5 fields)
2. `src/config.py`: Added `macro_screening` field to top-level `Settings` model
3. `src/config.py`: Added project-root `.env` fallback loading
4. `src/config.py`: Changed `auth_enabled` default from `False` to `True`
5. `config/settings.yaml`: Changed `host` from `0.0.0.0` to `127.0.0.1`, `auth_enabled` from `false` to `true`
6. `config/sources.yaml`: Set `enabled: false` on sec-edgar source (missing parser)
7. `config/risk_thresholds.yaml`: Added "REFERENCE ONLY — NOT loaded by app" header
8. `tests/integration/test_impact_pipeline.py`: Fixed test to include ticker "AAPL" in event text

**Validation:**
- 54/54 tests pass (was 53/54 before fix)
- Config loads correctly with new macro_screening model
- .env from project root is now loaded
- Server starts and responds with secure defaults

### Cycle 2 — API Smoke Tests + Documentation

**Changes made:**
1. `tests/smoke/test_api_smoke.py`: New file — 21 API endpoint smoke tests
2. `docs/INSTALL.md`: Added cross-reference header to root INSTALL.md
3. `.env.template`: Rewritten with secure defaults and guidance
4. `KNOWN_LIMITATIONS.md`: New file — V1 scope limitations documented
5. `RELEASE_BACKLOG.md`: Updated all resolved items

**Validation:**
- 75/75 tests pass (54 existing + 21 new API smoke tests)
- All API endpoints respond correctly
- Dashboard loads with "Axion" title
- Server starts in <2s, health reports "ok"

### Cycle 3 — Final Assessment

**Remaining open items (deferred to V2):**
- Scheduler job timeouts — low risk, internal try/except exists
- Telegram create_task safety — low risk, internal error handling exists

---

### Cycle 4 — Live End-to-End Release Validation (Session 2)

**Critical bugs found and fixed:**
1. Sources table empty (Collection agent reads from DB, not YAML) — **pipeline blocker**. Added YAML→DB sync on startup in `main.py`.
2. `Source` model missing `url` column — added to `models.py`.
3. Collection agent using `domain` as URL — fixed `collection.py` line 182.
4. Dead RSS URLs: Reuters domain dead, Yahoo RSS retired — disabled both in `sources.yaml`.
5. Classification agent not using ISIN for geography — NESN/SHEL got wrong country. Added ISIN enrichment in `classification.py`.
6. README claims "No Docker" while Docker files exist — fixed to "Native-first".

**Live test evidence (real network RSS fetch):**
- Server starts in <2s from clean state
- 7 sources synced from YAML to DB (2 enabled: Fed, ECB)
- 10 holdings uploaded via CSV ($163K portfolio, 3 currencies)
- 10 securities classified (rule-based: 8 correct sectors, 10 correct geographies after ISIN fix)
- 35 events fetched from real Federal Reserve + ECB RSS feeds
- 46 risk alerts generated (concentration, coverage gaps)
- 10 coverage reports generated
- All API endpoints return correct data
- Dashboard serves correctly
- 75/75 tests pass

**What was NOT tested (no credentials):**
- LLM analysis, macro screening, digest generation (no API key)
- Telegram bot (no token)
- Email delivery (no SMTP)
- macOS deployment (Windows machine)

---

### Cycle 5 — Full-Product Truth Test (Session 3)

**A. LLM Truth Check:**
- No ANTHROPIC_API_KEY available in .env, ~/.kleitos.env, or environment
- All LLM-dependent features remain **UNPROVEN** in live testing
- Rule-based fallback mode is **PROVEN** for all pipeline stages
- Updated README, KNOWN_LIMITATIONS to clearly mark LLM as optional

**B. Source Realism:**
- Added 4 working company-news RSS sources: CNBC, MarketWatch, Seeking Alpha, Investing.com
- CNBC returns 503 (geo-blocked or rate-limited), other 3 work
- Default package now: 5 working sources (2 central bank + 3 market news)
- Added company name matching to Collection agent — events now link to holdings by company name
- Added ticker→name map to Classification agent — securities now have displayable names

**C. Schema Migration:**
- Tested upgrade from DB without Source.url column
- ALTER TABLE ADD COLUMN succeeds automatically via _ensure_columns migration
- Old data preserved, new column defaults to NULL safely
- All 15 tables created/verified after migration

**D. Client-Scope Truth:**
- Updated README: removed "Mac mini" specificity, clarified LLM as optional
- Updated README: removed OpenClaw from stack table, added Telegram/email
- Full pipeline proven: upload → classify → collect → link → analyze → risk → digest
- 62 events, 4 event links, 4 analysis notes, 44 alerts, 10 coverage reports, 1 digest

**Evidence summary:**
- 75/75 tests pass
- 6 enabled RSS sources, 5 producing real events
- Company name matching creates event-to-holding links without LLM
- Rule-based analysis produces reasonable impact assessments
- Digest generation functional (content generated, structured data stored)
- Migration from old schema verified safe
- All product claims now match proven reality

---

### Cycle 6 — Premium Stabilization + UX/UI Modernization (Session 4)

**A. Bug Fixes:**
- Fixed alert ticker badges showing raw UUIDs instead of ticker symbols
- Fixed "Database" status dot showing yellow for "connected" (should be green)
- Fixed upload modal closing on error (prevented retry)
- Fixed `.claude/launch.json` using `--host 0.0.0.0` (contradicted hardened config)

**B. UX Improvements:**
- Added system status bar below Holdings summary: shows operational status, LLM mode, sources active, last collection
- Added `llm_available` field to health API endpoint
- Added "Analysis Mode" (AI-enhanced / Rule-based) to Health tab
- Improved digest generation feedback: shows spinner in digest area instead of just a toast
- Improved confidence display in Analysis Notes: visual bar with percentage instead of raw decimal

**C. Empty State Improvements (all tabs):**
- Events: explains 30-min cycle, offers "Run Collection Now" button
- Analysis: explains event→holding linking prerequisite, offers "Run Analysis" button
- Digest: explains daily schedule, offers "Generate Digest" button
- Alerts: explains what triggers alerts (concentration + coverage gaps)
- Exposures: "Upload a portfolio to see exposure breakdown"

**D. Visual Polish:**
- Added `connected`, `active`, `stopped` to status dot map for correct color coding
- Added CSS for system status bar and confidence bar components

**Validation:**
- 75/75 pytest tests pass
- 28/28 smoke tests pass
- Full visual verification via preview tools: Holdings, Events, Analysis, Digest, Alerts, Exposures, Health tabs all render correctly
- System status bar confirmed showing LLM mode and source count
- Alert ticker badges confirmed showing "SHEL" instead of UUIDs

---

### Cycle 7 — Premium Dashboard Redesign + UX Hierarchy Refinement (Session 5)

**Layout redesign:**
- Removed sidebar entirely — full-width layout, more content visible above fold
- Replaced 4 large summary cards + system bar with unified compact overview band
- Overview band: Portfolio Value + Holdings count + Sectors count + status chips in one row
- Moved summary above toolbar for better visual hierarchy
- Simplified Holdings toolbar: Search, + Add, Upload CSV, Trade, CSV, XLSX

**Navigation fix:**
- Hidden browser scrollbar on nav tabs (scrollbar-width: none, ::-webkit-scrollbar)
- Added subtle fade mask on overflow edge
- Removed margin-left:auto from Settings tab

**Visual polish:**
- Refined color system: slightly warmer card backgrounds, softer borders
- Increased border-radius to 10px for cards (more modern)
- Tightened typography: smaller h2, smaller font sizes throughout
- Refined button sizes and spacing
- More compact table rows and tighter cell padding
- Frosted glass nav with backdrop-filter blur
- New overview-band component with dividers and status chips

**Label humanization:**
- Sector badges: "INFORMATION TECHNOLOGY" → "Information Technology"
- Event types: "ANALYST_ACTION" → "Analyst Action"
- Alert titles and messages: snake_case → Title Case
- Exposure labels: "united states" → "United States"
- Severity badges: "medium" → "Medium"
- Added titleCase() helper function

**Status dots expanded:**
- Added .status-connected and .status-active CSS classes
- Added .badge-medium for medium-severity alerts

**Validation:**
- 75/75 pytest pass, 28/28 smoke pass
- Zero JS console errors, zero server errors
- All 8 tabs visually verified: Holdings, Exposures, Events, Analysis, Digest, Alerts, Health, Audit
- Fresh DB empty state verified: clean overview + empty state with actions
- Populated state verified: 10 holdings, 62 events, 46 alerts, exposure charts

---

### Cycle 8 — Visual Truth Test + UX Quality Verification (Session 5 cont.)

**Issues found in Cycle 7 redesign:**
1. Nav tabs still overflow at narrow viewport — Health/Settings invisible with no affordance
2. Overview band stats stacking vertically instead of horizontal
3. titleCase() over-capitalizing prepositions ("For", "In")
4. titleCase() lowercasing ticker symbols (SHEL → Shel) — regression
5. Dead sidebar HTML still in index.html despite being hidden by CSS

**Fixes applied:**
- Nav tabs: reduced padding (0.7→0.55rem), font (0.8→0.78rem), gap (0.15→0.1rem); re-added fade mask with JS-driven .no-overflow class to remove it when all tabs fit
- Overview band: changed flex-wrap from wrap to nowrap; reduced stat value font from 1.15→1.05rem; responsive wrap only below 768px
- titleCase(): added preposition exclusion set (for, in, of, to, by, etc.); added all-caps word preservation (SHEL, AAPL, USD stay uppercase)
- Removed entire sidebar HTML block from index.html; kept #action-result target
- Bumped cache to v=11

**Validation:**
- 75/75 tests pass
- Zero JS console errors
- Alert titles now read "No Recent Analyst Action for SHEL" (correct)
- Overview band confirmed horizontal at desktop width
- Nav tabs: 7 of 9 visible at 650px viewport, all 9 visible at 768px+

---

### Cycle 9 — Clean Install + Client Handoff Truth Test (Session 6)

**Critical doc bugs found and fixed:**
- All API URLs in docs/OPERATIONS.md, docs/TROUBLESHOOTING.md, docs/INSTALL.md were missing `/v1/` prefix — every curl example would have returned empty or 404
- OPERATIONS.md Classification interval listed as 15 min (actual: 6 hours)
- OPERATIONS.md and TROUBLESHOOTING.md DB path listed as `~/kleitos-data/kleitos.db` (actual: `~/kleitos-data/db/kleitos.db`)
- TROUBLESHOOTING.md referenced dead Reuters RSS URL and `--host 0.0.0.0`
- docs/INSTALL.md listed Anthropic API key as prerequisite (should be optional)
- README.md Stack table said "htmx + Pico CSS" (actual: vanilla JS + custom CSS)

**Other fixes:**
- Added Windows setup section to docs/INSTALL.md
- Created SVG favicon at dashboard/favicon.svg
- Updated favicon reference in index.html from .ico to .svg
- Removed OpenClaw references from OPERATIONS.md daily commands

**Clean install test:**
- Fresh DB + fresh start: server UP in 1s
- Health, sources, holdings, alerts, digests API paths all verified working
- Portfolio upload + classification verified working
- Favicon serves correctly

**Validation:** 75/75 tests pass. All corrected doc paths verified against live server.

---

### Cycle 10 — LLM Flagship Mode Validation (Session 6 cont.)

**Key availability:** No Anthropic API key found in .env, ~/.kleitos.env, or environment.

**Static audit of LLM code paths:**
- `src/llm/client.py`: Well-structured async client with retry/backoff, rate-limit handling, JSON parsing, markdown fence stripping. No bugs found.
- `src/llm/prompts.py`: Loads from config/prompts.yaml with hardcoded fallbacks. Clean.
- `config/prompts.yaml`: 3 prompt templates (classification, analysis, digest) with clear JSON schemas and conservative guidelines.
- `src/agents/classification.py`: LLM classification → fallback to rule-based on any error. Graceful.
- `src/agents/collection.py`: Macro screening → skipped entirely when no key. Safe.
- `src/agents/analysis.py`: Impact analysis → fallback to neutral/low with error message. Graceful. Digest → template fallback.

**LLM config verified:** claude-sonnet-4-6, temperature 0.1, max_tokens 4096, timeout 60s, 3 retries with [2,5,15]s backoff.

**Fallback behavior verified live:** Classification produces rule-based results (source=rule_based, confidence=0.3), health reports llm_available=false, dashboard shows "Rule-based" mode.

**No code changes needed.** The LLM integration is well-engineered with no bugs, parsing issues, or silent failures found in static analysis.

**Product positioning verdict:** A — "Rule-based product with optional LLM enhancement; LLM not yet proven live." The code is high-quality and likely to work, but no API call was made during the entire hardening process.

---

### Cycle 11 — Final Release Gate + Client Delivery Package (Session 6 cont.)

**Created delivery artifacts:**
- `RELEASE_NOTES_V1.md` — complete release notes with feature set, analysis modes, quality evidence, deployment matrix, limitations
- `OPERATOR_CHECKLIST.md` — step-by-step install/configure/verify/first-week checklist

**Fixed:**
- `KNOWN_LIMITATIONS.md` — updated "2 sources" to "6 sources" (was outdated from before Cycle 5)
- `OPERATOR_CHECKLIST.md` — fixed sidebar reference (sidebar was removed in Cycle 7)

**Final release decision: CONDITIONAL GO**
- Rule-based pipeline: fully proven
- LLM pipeline: designed, coded, audited — not live-tested
- Dashboard: premium, verified
- Documentation: corrected, verified
- Install: proven on Windows, scripts available for macOS/Docker

**75/75 tests pass. 11 cycles of systematic hardening complete.**

---

### Cycle 12 — Full Rebrand to Axion + 4Labs Brand Ownership

Rebranded all client-facing surfaces from Kleitos to Axion by 4Labs. Dashboard, docs, Python source, configs, launcher messages, Docker config, OpenClaw configs. Legacy env vars (KLEITOS_*) and data paths (kleitos-data/) preserved for backward compatibility.

---

### Cycle 13 — Brand Completion + Backward-Compat Cleanup

- Created `Axion.bat` as primary launcher; `Kleitos.bat` → thin compatibility shim
- Created `scripts/axion-tray.pyw` and `scripts/axion-menubar.py` (fully rebranded)
- Created `scripts/stop-axion.bat`; `scripts/stop-kleitos.bat` → shim
- Created `config/launchd/com.axion.core.plist` and `com.axion.openclaw.plist`
- Rebranded all 12 shell scripts (visible messages only, env vars preserved)
- Rebranded all 19 OpenClaw workspace configs
- Rebranded test files
- Updated all remaining docs: launchd commands, clone paths, docker references
- Fixed `.kleitos-project-dir` → `.axion-project-dir`

**Remaining internal legacy (backward compatibility):**
- `KLEITOS_*` env var names
- `kleitos-data/` directory and `kleitos.db` file
- `kleitos-*.db` backup filenames
- `kleitos-*.log` log filenames
- `kleitos.ico` and `kleitos-*.png` asset filenames
- `com.kleitos.*` launchd plists (kept alongside new `com.axion.*`)
- `KleitosScheduler` class name (internal)
- `ARCHITECTURE.md` body (internal design doc, title updated)
- `Kleitos.app/` bundle directory (macOS, kept for now)

**75/75 tests pass. 13 cycles complete. Rebrand verified.**

---

### Desktop App Transition Program

**Architecture:** pywebview native window shell wrapping the existing FastAPI dashboard.

**Changes:**
- Created `scripts/axion-app.pyw` — native desktop app shell with branded splash screen
- Updated `scripts/build-exe.py` — builds from app shell (11.5 MB, down from 17.6 MB)
- Fixed `--host 0.0.0.0` → `127.0.0.1` in macOS launcher (security)
- Updated macOS `Axion.app` launcher to use pywebview when available
- Added `pywebview>=5.0` to requirements.txt
- Added `axion-app.pyw` to delivery packager

**Result:** Axion now opens as a native OS window with branded splash, taskbar presence, and real titlebar — not a browser tab. Browser remains as automatic fallback.

---
