# Axion Release Decisions Log

## 2026-03-18

### Decision 1: De-scope SEC EDGAR parser from V1
- **Rationale:** SEC EDGAR requires a custom API parser (not RSS). The source is listed in sources.yaml with `enabled: true` but the parser doesn't exist. Rather than building a complex parser, disable the source and document it as V2.
- **Action:** Set `enabled: false` in sources.yaml for sec-edgar, add comment about V2.

### Decision 2: De-scope Finnhub parser from V1
- **Rationale:** Already `enabled: false` in sources.yaml. Parser doesn't exist. Safe to leave disabled. Document as V2.
- **Action:** Add comment in sources.yaml about V2 implementation.

### Decision 3: De-scope event clustering from V1
- **Rationale:** Dedup works (exact hash + near-duplicate). Clustering is aspirational. The no-op mark_cluster() is safe (only logs). Not blocking.
- **Action:** Leave as-is, document as V2 feature.

### Decision 4: De-scope PriceHistory and PortfolioSnapshot from V1
- **Rationale:** Schema exists, no code populates them. Not part of core pipeline. Document as V2 features.
- **Action:** Add note in KNOWN_LIMITATIONS.md.

### Decision 5: First-client scope definition
- **Core V1 scope:** RSS-based news collection, rule-based + LLM event matching, impact analysis, risk monitoring, daily digests, web dashboard, optional Telegram bot.
- **NOT in V1:** SEC EDGAR, Finnhub, price feeds, event clustering, OpenClaw (optional/unsupported).
- **Deployment:** Windows (primary, tested), macOS (documented), Docker (available).

### Decision 6: Config loading fix approach
- **Rationale:** The `.env` in project root is a common developer convention. Config.py should load from project-root `.env` as well as `~/.kleitos.env`.
- **Action:** Update `_build_settings()` to check both `~/.kleitos.env` and `{PROJECT_ROOT}/.env`, with home-dir taking precedence.

### Decision 7: Auth should be enabled by default
- **Rationale:** Shipping with auth disabled is a security risk. For first-client, auth should be on with a generated key, or at minimum clearly documented.
- **Action:** Change default to `auth_enabled: true`, auto-generate key if not set, document.
