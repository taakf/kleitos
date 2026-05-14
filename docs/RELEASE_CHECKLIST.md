# Axion — Release readiness checklist

What to verify before declaring a build customer-ready. Run this in order. Don't skip steps.

## A. Source state

- [ ] `git status` is clean.
- [ ] `git log --oneline -5` matches the release notes.
- [ ] You are on the branch you intend to release (`main`, by default).
- [ ] No uncommitted secrets in `.env`, `~/.axion.env`, or anywhere in the tree.

```bash
git status
git log --oneline -5
grep -RIn "sk-ant-\|sk-proj-\|ghp_" --include='*.py' --include='*.md' --include='*.sh' --include='*.ps1' src scripts docs README* 2>/dev/null
```

The grep should return **no matches**.

## B. Static checks

Run from the project root with the venv active.

- [ ] **All Python files compile**
  ```bash
  python -m compileall -q src scripts tests
  ```
  Must exit 0.

- [ ] **Tests pass**
  ```bash
  python -m pytest -q
  ```
  All tests green.

- [ ] **Linter clean** (warnings allowed, no fatals)
  ```bash
  python -m ruff check src tests scripts
  ```

- [ ] **No new security findings**
  ```bash
  python -m bandit -q -r src scripts
  ```

- [ ] **Bash launcher is syntactically valid**
  ```bash
  bash -n scripts/run_local.sh
  ```

- [ ] **PowerShell launcher is syntactically valid** (on Windows, or anywhere with PowerShell installed)
  ```powershell
  $tokens = $null; $errors = $null
  [System.Management.Automation.Language.Parser]::ParseFile('scripts\run_local.ps1', [ref]$tokens, [ref]$errors)
  $errors
  ```
  Output must be empty.

## C. End-to-end smoke

- [ ] **Local smoke test passes**
  ```bash
  python scripts/smoke_local.py
  ```
  Must report `16/16 passed`.

- [ ] **Database safety regressions pass**
  ```bash
  python -m pytest -q tests/unit/test_phase3_migration_safety.py
  ```
  Must report all green. Covers:
  - Pre-migration backup is created on upgrades and skipped on no-ops.
  - Backup failure stops migration before any schema change.
  - Newer-DB raises a typed `DatabaseVersionTooNewError` and does not modify the file.
  - Corrupt DB raises `DatabaseCorruptError` and the file is byte-identical.
  - v3–v8 migrations are idempotent.
  - `/api/v1/system/recovery` returns the correct structured state for ok / version_too_new / corrupt.
  - `scripts/migrate.py` exits 0 / 2 / 3 / 4 according to the documented contract.

- [ ] **Source health regressions pass**
  ```bash
  python -m pytest -q tests/unit/test_phase7_sources.py
  ```
  Must report all green. Covers:
  - `scrub_source_error()` masks URL-embedded keys, Bearer tokens, and vendor-token shapes.
  - `classify_fetch_outcome()` maps HTTP 401/403/429/5xx + timeout / DNS / parser failures to the typed vocabulary.
  - Every YAML-declared source has the required fields.
  - `sec-edgar` carries `unsupported: true`; `finnhub-news` and `newsapi-general` declare `auth_env_var`.
  - `GET /api/v1/sources/health` returns the normalized list + per-status summary; never includes raw API keys; reports `Unsupported` for sec-edgar with a disabled toggle; reports `Missing key` for newsapi/finnhub when their env vars are empty.
  - Finnhub parser handles a valid array, an empty array, an error dict, and articles missing fields.
  - One broken source does not stop collection for the others.
  - Support bundle redacts URL-embedded keys + lists source health.
  - `/api/v1/system/diagnostics` reports `sources_by_status` with the normalized vocabulary.
  - Settings → Sources UI uses the Phase 7 status vocabulary and `Auth env var` column.
  - Customer docs name `NEWSAPI_KEY` and `FINNHUB_KEY` and never claim Bloomberg / FactSet / ATHEX corporate events.

- [ ] **Insights history + saved-view regressions pass**
  ```bash
  python -m pytest -q tests/unit/test_phase14_insights_history.py
  ```
  Must report all green. Covers:
  - Navigation: ``intelligence`` is in ``_KNOWN_SURFACES``; ``_APPROVED_FILTERS[("intelligence", "overview")]`` lists `category` / `severity` / `time_window_days` / `time_window` / `include_ai` / `ai`.
  - ``describe_view`` renders ``Insights``, ``Insights · Overview``, ``Insights · Overview · Critical``, ``Insights · Overview · News impact · Last 7 days``, and ``Insights · Overview · AI narration on`` cleanly without doubling labels.
  - ``GET /api/v1/intelligence/insights/history`` returns ``portfolio_id`` / ``window_days`` / ``generated_at`` / ``items[]`` / ``daily_counts[]`` / ``summary``; empty portfolio returns honest empty response; portfolio isolation holds; the ``state`` / ``category`` / ``severity`` / ``days`` filters all work.
  - Each item carries a typed deep link (``surface`` enum) routed by category (news_impact→events detail, corporate_event→Events tab, alert→Alerts, revenue/listing→Portfolio Exposures, factor→Operator factors).
  - History endpoint never leaks AI prompt body or narration text — snapshot rows only.
  - Dashboard markup carries the history deck container, the 7d/30d/90d pills, the state filter, the summary strip, the sparkline element, and the empty-state copy.
  - ``_captureCurrentViewPayload`` produces a payload with ``surface="intelligence"`` + ``subtab="overview"`` when the Overview sub-tab is active; ``_applyTargetFilter`` restores those filters.

- [ ] **Insights export + shareable state regressions pass (Phase 15)**
  ```bash
  python -m pytest -q tests/unit/test_phase15_insights_export.py
  ```
  Must report all green. Covers:
  - Navigation: ``history_state`` (with alias ``state``) is in ``_APPROVED_FILTERS[("intelligence", "overview")]``; ``validate_filters`` strips unknown keys; ``describe_view`` renders *New only* / *Escalated only*; ``encode_nav_hash`` ↔ ``decode_nav_hash`` round-trip the new filter without dropping it.
  - ``POST /api/v1/intelligence/insights/export`` returns CSV with the fixed 17-column header row (``section,category,severity,state,title,summary,why_it_matters,recommended_action,affected_holdings,confidence,first_seen_at,last_seen_at,notified_at,deep_link_label,deep_link_surface,deep_link_subtab,source_type``) and an ``axion-insights-overview-YYYYMMDD-HHMMSS.csv`` filename.
  - ``GET /api/v1/intelligence/insights/export.json`` returns the stable JSON envelope (``portfolio_id`` / ``generated_at`` / ``window_days`` / ``filters`` / ``summary`` / ``current_cards`` / ``history`` / ``daily_counts`` / ``grounding_status`` / ``warnings`` / ``coverage`` / ``last_generated_at``).
  - Filters pass through: ``category`` / ``severity`` / ``history_state`` / ``days`` narrow both responses; portfolio isolation holds (a second portfolio's rows never appear).
  - Privacy: neither response body contains ``GROUNDING CONTRACT`` / ``BEGIN PDF`` / ``api_key=`` / ``Bearer `` / ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` / ``TELEGRAM_BOT_TOKEN`` / ``.axion.env``; the ``_safe_str`` scrubber maps any forbidden substring to ``[redacted]``.
  - Dashboard markup carries ``insights-export-csv-btn``, ``insights-export-json-btn``, ``insights-copy-link-btn`` inside the ``#subtab-overview`` block; app.js exposes the API constants and wires each button; the copy-share helper routes through the existing Phase 9R ``_copyDeepLink``.
  - Surface lock-in: ``_KNOWN_SURFACES`` is unchanged (Phase 15 added zero new surfaces).

- [ ] **Insights notifications regressions pass**
  ```bash
  python -m pytest -q tests/unit/test_phase13_insight_notifications.py
  ```
  Must report all green. Covers:
  - Fingerprint stability: identical content → identical fingerprint; severity/evidence/title changes move it; AI summary rewording does NOT move it.
  - `card_key` stable across re-runs and independent of the InsightCard `id` field.
  - Migration `v11` creates `insight_snapshots` with the required columns + indexes + uniqueness constraint; `run_migrations()` is idempotent.
  - Notifier classifies cards as `new` / `escalated` / `unchanged` / `first_run`; idempotent on repeat runs.
  - Snapshot rows carry no AI prompt body / narration text.
  - Inbox shaper surfaces only `new` / `escalated` insights above the `medium` floor; deep links resolve to a navigation target.
  - Telegram delivery is a no-op when not configured; when mocked-configured, the dispatcher delivers high+ severity new/escalated cards.
  - Digest builder attaches a deterministic `top_insights` list (no AI summary leakage).
  - `POST /api/v1/intelligence/insights/run` persists snapshots and returns the structured summary; `GET /insights/last-run` returns the most recent timestamp.
  - Scheduler `setup({})` registers the `insights_generation` job id.
  - Dashboard markup carries the **Run now** button + **Last generated** stamp + notification pill CSS classes.

- [ ] **Insights overview regressions pass**
  ```bash
  python -m pytest -q tests/unit/test_phase12_insights.py
  ```
  Must report all green. Covers:
  - The :class:`InsightCard` Pydantic shape validates and the navigation helper recognises the new ``corporate-events`` and ``settings`` surfaces.
  - Empty-portfolio insight response returns a helpful data-gap onboarding card with no fake content.
  - High-materiality News linked to a holding produces a ``news_impact`` card with structured evidence + a deep link to the News detail modal.
  - Upcoming Corporate Events produce a ``corporate_event`` card; alerts produce ``alert`` cards; macro-factor links produce ``factor_sensitivity`` cards.
  - Listing-country and Revenue-geography cards stay separate; revenue-geography never carries listing-country evidence and vice-versa.
  - Missing revenue-geography yields a clean data-gap card; an upload flips the card to a region-named insight.
  - AI narrator: missing key path returns deterministic cards with ``grounding_status="ai_unavailable"``; mocked success preserves evidence + deep_links and flips ``source_type="ai_narrative"``; rewrites that mention untrusted tickers are discarded; raises become ``grounding_status="ai_failed"`` with a warning.
  - ``/api/v1/intelligence/insights`` returns a stable shape with no secrets; the legacy ``/intelligence/summary`` shape is unchanged.
  - Dashboard markup carries the new Overview sub-tab, coverage strip, grounding banner, refresh button, category + severity filters, AI-narrate toggle.
  - Support bundle does not inline the narration prompt body or any insight card title.

- [ ] **Revenue-geography AI extraction regressions pass**
  ```bash
  python -m pytest -q tests/unit/test_phase11_revenue_geography_ai.py
  ```
  Must report all green. Covers:
  - The extraction prompt forbids inference from headquarters, country of incorporation, ISIN prefix, listing exchange, and customer names; mandates explicit-only numbers; returns an empty candidate list when the report has no regional revenue.
  - Typed status path: `missing_key` when no LLM provider is configured, `disabled`/`extraction_failed`/`unsupported_file`/`no_revenue_geography_found`/`success` map to the right outcomes.
  - Mocked success preserves confidence + evidence text + page number per candidate; malformed LLM output → `extraction_failed`; negative shares are dropped with row errors.
  - `POST /api/v1/exposures/revenue-geography/extract` never persists; `POST /confirm-extraction` persists with `source_type="ai_extracted"` and goes through the same ISIN-first matcher as Phase 10.
  - Multi-portfolio isolation holds across both extract + confirm.
  - The dashboard import dialog carries both tabs (Manual CSV + AI extract from report), the review table, and the *Nothing is saved until you click Confirm* language.
  - The support bundle reports `revenue_geography` counts + `source_type` breakdown but never inlines uploaded PDF bytes or region row bodies.

- [ ] **Revenue-geography foundation regressions pass**
  ```bash
  python -m pytest -q tests/unit/test_phase10_revenue_geography.py
  ```
  Must report all green. Covers:
  - Migration `v10` creates `revenue_geography` with the required columns, indexes, unique constraint, and `CHECK (revenue_share >= 0)`.
  - `parse_revenue_share` accepts `0.45` / `45` / `45%` and rejects negatives; `normalize_region` resolves common aliases (EMEA, APAC, US → North America, …).
  - `validate_company_allocations` emits soft warnings for sum < 95 % and sum > 105 % without blocking the import.
  - `compute_portfolio_revenue_exposure` aggregates by holding weight, surfaces an explicit `"Revenue geography not uploaded"` bucket for holdings without rows, and **never** falls back to listing country.
  - Manual CSV import: ISIN-first then ticker matching, per-row errors, dedup on repeat upload, URL scrubbing of `apiKey=` / `token=` style query params.
  - API: `GET /api/v1/exposures/listing-country` returns `data_source="isin_prefix_or_venue"`; `GET /api/v1/exposures/revenue-geography` returns the typed `status` (`missing` / `partial` / `available`); `POST /import` returns the per-row summary; `GET /missing` lists holdings without rows. Multi-portfolio isolation holds.
  - Legacy `GET /api/v1/portfolio/exposure?dimension=geography` still works untouched (back-compat).
  - Grounded AI context (`GroundedEventContext`) now carries `holding_revenue_geography_status` and `holding_revenue_breakdown`; prompts say "not uploaded — do not infer from listing country" when the status is `missing`.
  - Dashboard markup carries the new Revenue geography card and CSV import dialog; the legacy "Geography" customer label is replaced by "Listing country" in `loadExposures`.

- [ ] **Corporate-events foundation regressions pass**
  ```bash
  python -m pytest -q tests/unit/test_phase9_corporate_events.py
  ```
  Must report all green. Covers:
  - Migration `v9` creates `corporate_events` with the required columns and indexes; `run_migrations()` is idempotent.
  - `ISIN_COUNTRY_MAP` now resolves `GR` → `greece` and the new `src.intelligence.listing` helper detects ATHEX-listed holdings via venue alias, ISIN prefix, and ticker suffix in that priority order.
  - `config/sources.yaml` declares the `athex-corporate-events` row as `type: corporate_events`, `unsupported: true`, `enabled: false`, with a customer-safe note pointing at the manual-import path.
  - `fetch_athex_events()` returns a typed `unsupported`/`degraded` result by default — no fake events, ever.
  - `parse_csv()` enforces required fields, normalises canonical/aliased event types and ISO/European dates, scrubs URLs, and auto-fills `exchange=ATHEX` when the listing detector says so.
  - `import_csv()` matches ISIN-first then ticker, keeps unmatched rows with `match_method='unmatched'`, and dedupes on repeated imports.
  - `GET /api/v1/corporate-events` honours every filter (month, event_type, ticker, holding_id, isin, exchange, date_from/to), returns a bare list by default, an `{items,total,limit,offset,has_more}` envelope under `?envelope=true`, and always sets `X-Total-Count` / `X-Has-More`.
  - `POST /api/v1/corporate-events/import` returns row-level errors; `POST /api/v1/corporate-events/refresh` returns the honest `unsupported`/`degraded` body.
  - Multi-portfolio isolation: pA rows never bleed into pB queries.
  - Source URLs are scrubbed of `apiKey=` / `token=` patterns on every list + detail response.
  - Dashboard markup carries the top-level `Events` tab (`data-tab="corporate-events"`, `tab-corporate-events` panel), the calendar grid, filter controls, and the Import/Detail dialogs. The Insights → News surface is unchanged.

- [ ] **News-tab hardening regressions pass**
  ```bash
  python -m pytest -q tests/unit/test_phase8_news.py
  ```
  Must report all green. Covers:
  - `_scrub_url()` masks `apiKey=`, `api_key=`, `token=`, `access_token=`, `auth=`, `secret=` query parameters and is idempotent on innocent URLs.
  - `GET /api/v1/events` honours the new filters: `q`, `source_id`, `holding_id`, `ticker`, `event_type`, `factor_key`, `linked_only`, `materiality_min`, `confidence_min`, `date_from`.
  - Default response stays a bare list; `?envelope=true` returns `{items, total, limit, offset, has_more}`.
  - `X-Total-Count` / `X-Has-More` headers are always set and match the envelope shape.
  - List + detail + recent endpoints all return scrubbed URLs.
  - `describe_view` renders the new filter keys as `News · Source: …`, `News · Ticker: …`, `News · Type: …`, `News · Factor: …`, `News · Materiality: …`, `News · Linked only`, `News · Search: …`.
  - `validate_filters` accepts the new News keys and still strips unknown keys.
  - Dashboard markup carries the new filter ids and Reset button; the customer label stays **News** (the internal DOM ids stay `events-…`).
  - The JS uses a debounced backend search; the legacy client-side `allEvents.filter` substring path is gone.
  - The CSS exposes the filter-bar, range-pill, and status-chip classes the JS renderer emits.

- [ ] **AI provider regressions pass**
  ```bash
  python -m pytest -q tests/unit/test_phase6_providers.py
  ```
  Must report all green. Covers:
  - Anthropic / OpenAI / Gemini `test_connection()` missing-key path.
  - Mocked successful provider response → status `active`.
  - Mocked auth failure → status `invalid_key` (no exception text leaked).
  - Mocked rate-limit → status `quota_issue`.
  - Mocked network error → status `unreachable`.
  - `/api/v1/settings/test-provider` rejects unknown providers with 400.
  - `/api/v1/settings/test-provider` never returns an API key string.
  - `scrub_secrets()` removes Anthropic / OpenAI / Gemini / Telegram patterns.

- [ ] **Support tooling regressions pass**
  ```bash
  python -m pytest -q tests/unit/test_phase4_support_diagnostics.py
  ```
  Must report all green. Covers:
  - `scripts/rotate_logs.py` rotates oversized files and leaves small / unknown files alone.
  - `scripts/support_bundle.py` produces a zip with the expected metadata + log tails.
  - Support bundle excludes `.db` files and raw `.env`.
  - Support bundle redacts Anthropic / OpenAI / Telegram-style secrets by both key name and value pattern.
  - `/api/v1/system/diagnostics` returns 200 with redacted structured snapshot.
  - Diagnostics endpoint handles missing DB and corrupt DB without crashing.
  - First-run welcome card markup carries the `data-first-run="empty"` marker, mentions the offline CSV path, labels AI as optional, points at `sample_portfolio.csv`, and does not promise live prices.

## D. Fresh-machine simulation

Wipe everything and run as if a customer just downloaded the project.

- [ ] Run reset: `rm -rf .venv ~/axion-data ~/.axion.env`
- [ ] Run launcher: `./scripts/run_local.sh` (or `scripts\run_local.ps1`)
- [ ] Launcher reports `Axion is running.` within 2 minutes.
- [ ] Browser opens `http://127.0.0.1:7777/dashboard/` automatically (macOS / Windows).
- [ ] Dashboard renders without console errors.
- [ ] Portfolio tab shows the **default** portfolio (id `default`, name "Main Portfolio").
- [ ] Empty states are present and graceful (no "undefined", no stack traces).
- [ ] Health endpoint reports `status: ok` or `degraded`, never a 500.
  ```bash
  curl -s http://127.0.0.1:7777/api/v1/health | python -m json.tool
  ```

## E. CSV import flow

- [ ] Drag `sample_portfolio.csv` into the dashboard.
- [ ] Review screen shows 10 rows.
- [ ] Click Import → success toast, rows appear in the Holdings table.
- [ ] Portfolio → Exposures shows non-zero sector and currency breakdowns.

## F. Settings / AI

- [ ] **Without** an API key:
  - Settings → AI Provider shows "Not configured."
  - POST `/api/v1/settings/test-provider` returns `status: disabled` or `unreachable` (never 500).
  - Assistant tab degrades gracefully (no fake LLM output).
- [ ] **With** a real key (if you're shipping with one):
  - Settings → Test reports `active`.
  - Assistant tab returns real model responses.

## G. Stop / restart

- [ ] Ctrl+C in the launcher cleanly shuts down (no orphaned uvicorn process).
- [ ] Relaunching keeps the imported holdings and any settings.

## H. Doc consistency

- [ ] `README.md` points at `README_LOCAL.md` and `docs/CUSTOMER_QUICKSTART.md`.
- [ ] `README_LOCAL.md` matches what the launchers actually do.
- [ ] `docs/CUSTOMER_QUICKSTART.md` mentions the AI features as optional.
- [ ] `KNOWN_LIMITATIONS.md` is current — no removed features still listed.

## I. Repo hygiene

- [ ] No stale duplicate source trees in the project root (no `Axion/`, no `Axion-Installers/`, no `Axion.app/` unless it has a real binary).
- [ ] `dist/` is either empty or gitignored.
- [ ] `~/axion-data/`, `.venv/`, `__pycache__/`, `*.db` are gitignored.
- [ ] `gh repo view --json defaultBranchRef` confirms `origin/main` matches local HEAD.

## J. Release artifact (only if shipping a zip)

- [ ] `python scripts/build_release_zip.py` produces `dist/axion-windows.zip` and `dist/axion-macos.zip`.
- [ ] Each zip contains `src/`, `dashboard/`, `config/`, `scripts/run_local.*`, `requirements.txt`, `sample_portfolio.csv`, `README_LOCAL.md`, `docs/`, `.env.template`.
- [ ] Each zip **excludes** `.git`, `.venv`, `__pycache__`, `dist`, `~/axion-data`, and any `Axion/` / `Axion-Installers/` duplicates.
- [ ] Extracting the zip on a clean machine, then running the launcher, reaches a healthy dashboard.

## Cross-platform validation

Before publishing a release, the GitHub Actions workflow **`Release Local App Validation`** must pass on both runners:

- `windows-latest`
- `macos-latest`

It runs automatically on every pull request to `main`, and can be triggered manually via the **Actions → Release Local App Validation → Run workflow** button. The workflow definition is at [`.github/workflows/release-local-app.yml`](../.github/workflows/release-local-app.yml).

What the workflow proves on **each** OS:

- [ ] Python compilation succeeds (`compileall`)
- [ ] Test suite passes (`pytest`)
- [ ] In-process end-to-end smoke passes (`scripts/smoke_local.py` — 16 checks)
- [ ] Launcher syntax is valid (`bash -n` on macOS, PowerShell AST parse on Windows)
- [ ] Release zips build and verify (`scripts/build_release_zip.py`)
- [ ] Real local server startup works (`scripts/smoke_server_startup.py` — boots uvicorn on a temp DB, hits `/api/v1/health` and `/dashboard/`, then shuts down cleanly)
- [ ] Smoke from inside the extracted release zip passes (proves the zip itself is shippable)

If both jobs are green, **the Windows path is fully validated**. There is no separate manual Windows validation step required for release.

### Manual fallback if CI is unavailable

If for any reason CI cannot run (e.g. the workflow file is broken, GitHub is down, or you need to release urgently from a fork), reproduce the same gates manually:

1. On a fresh Windows 10/11 machine with Python 3.11+ on PATH:
   ```powershell
   python -m pip install -r requirements.txt pytest pytest-asyncio
   python scripts\smoke_local.py             # expect 16/16
   python scripts\build_release_zip.py
   python scripts\smoke_server_startup.py    # expect all checks PASS
   ```
2. On a fresh macOS machine with Python 3.11+:
   ```bash
   python -m pip install -r requirements.txt pytest pytest-asyncio
   python scripts/smoke_local.py             # expect 16/16
   python scripts/build_release_zip.py
   python scripts/smoke_server_startup.py    # expect all checks PASS
   ```
3. Tester sign-off:
   - [ ] Tester name: ____________
   - [ ] OS / version: ____________
   - [ ] Python version: ____________

---

## K. Sign-off

| Check | Owner | Date |
|-------|-------|------|
| Static checks (B) | | |
| Smoke test (C) | | |
| Fresh-machine sim (D) | | |
| CSV import (E) | | |
| Settings/AI (F) | | |
| Stop/restart (G) | | |
| Cross-platform CI green (`Release Local App Validation` — both `macos-latest` and `windows-latest`) | | |

When every box above is ticked, the build is **customer-ready**.

## macOS launcher options

This repo ships two macOS launchers. They now share the same data-dir convention (`~/axion-data` default, `~/kleitos-data` honoured for back-compat), but they target different audiences.

| Launcher | When to use | Status |
|----------|-------------|--------|
| **`./scripts/run_local.sh`** | Anyone with a terminal. Foreground process, Ctrl+C to stop. | **Recommended.** Verified by the smoke test. |
| **`Axion.app`** | Customers who prefer double-clicking from Finder. Installs a launchd auto-start agent. | Working, but not code-signed. Gatekeeper requires right-click → Open on first launch. Verified manually only. |

If you are unsure, recommend `run_local.sh`. It has the smaller blast radius (no launchd auto-start, no `/Applications` install, no Finder PATH issues).
