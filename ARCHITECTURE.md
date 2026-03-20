# AXION: Portfolio Intelligence System — by 4Labs
# Master Architecture & Implementation Plan
# (Originally developed under the codename "Kleitos")

**Version**: 1.0.0
**Date**: 2026-03-12
**Target Deployment**: Mac mini (Apple Silicon), 24/7 operation, OpenClaw Multi-Agent Command Center

---

## Table of Contents

1. [Executive Architecture Summary](#1-executive-architecture-summary)
2. [Assumptions and Open Decisions](#2-assumptions-and-open-decisions)
3. [Final Architecture Map](#3-final-architecture-map)
4. [Multi-Phase Implementation Roadmap](#4-multi-phase-implementation-roadmap)
5. [Recommended Implementation Order](#5-recommended-implementation-order)
6. [Data Model / Schema Plan](#6-data-model--schema-plan)
7. [Agent Contracts](#7-agent-contracts)
8. [Impact Mapping Engine Design](#8-impact-mapping-engine-design)
9. [OpenClaw Multi-Agent Command Center Design](#9-openclaw-multi-agent-command-center-design)
10. [Source Control / Registry Design](#10-source-control--registry-design)
11. [Security and Permission Model](#11-security-and-permission-model)
12. [Mac Mini Deployment and Operations Design](#12-mac-mini-deployment-and-operations-design)
13. [Testing and QA Strategy](#13-testing-and-qa-strategy)
14. [Observability / Operations Plan](#14-observability--operations-plan)
15. [Reporting and UI Plan](#15-reporting-and-ui-plan)
16. [MVP vs V2 vs V3 Scope](#16-mvp-vs-v2-vs-v3-scope)
17. [Implementation Risks and Failure Modes](#17-implementation-risks-and-failure-modes)
18. [Final Recommended Stack and Repo Structure](#18-final-recommended-stack-and-repo-structure)
19. [Step-by-Step Build Sequence](#19-step-by-step-build-sequence)
20. [Definition of Done](#20-definition-of-done)
21. [Client Mac Mini Installation Instructions](#21-client-mac-mini-installation-instructions)
22. [First 14 Days Execution Plan](#22-first-14-days-execution-plan)
23. [Questions That Should Be Answered Before Coding Starts](#23-questions-that-should-be-answered-before-coding-starts)

---

## 1. Executive Architecture Summary

### System Shape

Kleitos is a **locally-deployed portfolio intelligence platform** running natively on a Mac mini. It consists of three layers:

**Layer 1 — Kleitos Core (Python backend)**
A FastAPI application containing the portfolio ledger, security master, event store, impact mapping engine, source registry, and all business logic. It exposes a REST API for data access and agent operations. It runs as a native Python process managed by macOS `launchd`.

**Layer 2 — Kleitos Agents (6 specialized agents + 1 commander)**
Six domain-specific agents implement the intelligence pipeline: Intake, Classification, Collection, Coverage QA, Analysis, and Risk. Each agent is a Python module callable both programmatically (via the scheduler and API) and through OpenClaw skills. A seventh "Commander" agent orchestrates the client-facing experience.

**Layer 3 — OpenClaw Multi-Agent Command Center**
OpenClaw runs locally on the Mac mini as the client's primary interface. It provides natural-language chat (via Telegram, Signal, WhatsApp, Discord, or WebChat), organized updates, and alert delivery. Each of the 6 domain agents has an OpenClaw workspace with skills that call the Kleitos API. The Commander agent is the client's main conversational partner, delegating to specialists as needed.

**Layer 4 — Dashboard (Web UI)**
A lightweight web dashboard served by FastAPI provides visual views: portfolio holdings, exposures, digests, alerts, audit trails, and system health. Accessible via browser at `http://localhost:7777`. This supplements the chat interface for structured data exploration.

### Agent Structure: Preserved

The original 6-agent structure is **fully preserved** with no changes:

| # | Agent | Role |
|---|-------|------|
| 1 | Intake & Reconciliation | Parses portfolio inputs, standardizes identifiers, reconciles against ledger |
| 2 | Classification & Exposure | Classifies holdings, produces exposure views |
| 3 | News & Event Collection | Fetches from approved sources, deduplicates, timestamps, classifies, maps |
| 4 | Coverage QA / Completeness | Checks coverage gaps, verifies key events captured |
| 5 | Portfolio Analysis | Analyzes impact like a portfolio manager, distinguishes noise from signal |
| 6 | Risk & Alerting | Monitors concentration, exposure, calendar risks, thesis drift |

**Addition**: A 7th "Commander" agent is added as the client-facing orchestrator within OpenClaw. This does not replace any of the 6 core agents — it coordinates them and presents results to the client.

### How Mac Mini + OpenClaw + Command Center Fit Together

```
Mac mini (24/7, Apple Silicon, macOS)
├── launchd manages:
│   ├── kleitos-core (FastAPI on port 7777)
│   ├── kleitos-scheduler (APScheduler for periodic jobs)
│   └── openclaw gateway (OpenClaw on default port)
├── Data lives at: ~/kleitos-data/
│   ├── db/kleitos.db (SQLite)
│   ├── logs/
│   ├── backups/
│   └── exports/
├── OpenClaw config at: ~/.openclaw/
│   ├── openclaw.json (multi-agent routing)
│   └── workspaces/ (7 agent workspaces)
└── Client accesses via:
    ├── Chat: Telegram/Signal/WhatsApp/Discord/WebChat
    ├── Dashboard: http://localhost:7777 (or LAN IP)
    └── Both are the "Command Center"
```

---

## 2. Assumptions and Open Decisions

### Assumptions Made

| # | Assumption | Rationale |
|---|-----------|-----------|
| A1 | Mac mini is Apple Silicon (M1/M2/M3/M4) | Most common current Mac mini. Intel path noted where different. |
| A2 | macOS 14 (Sonoma) or later | Required for current Homebrew, Python 3.11+, and OpenClaw. |
| A3 | Single user / single client | No multi-tenancy needed. Simplifies auth and data model. |
| A4 | Portfolio size < 500 holdings | SQLite is more than sufficient. No need for Postgres. |
| A5 | < 50 approved news sources | Source registry is file-configured, not a separate service. |
| A6 | Claude (Anthropic API) is the primary LLM | Best reasoning for financial analysis. OpenClaw supports it natively. |
| A7 | Client has Anthropic API key | Required for LLM reasoning in agents. |
| A8 | Internet access is available | Required for news fetching and LLM API calls. |
| A9 | Portfolio data is provided manually or via file upload | No direct brokerage API integration in MVP. |
| A10 | One messaging channel for client comms | Client picks one: Telegram, Signal, or WebChat. |
| A11 | English-language news sources | Multilingual support deferred to V2. |
| A12 | Daily digest cycle is the primary rhythm | Real-time streaming deferred to V2. |

### Decisions to Lock Down Early

| # | Decision | Options | Recommendation |
|---|----------|---------|----------------|
| D1 | Primary chat channel | Telegram / Signal / WhatsApp / WebChat | Client choice. WebChat is easiest for MVP testing. |
| D2 | LLM provider | Anthropic / OpenAI / Local | Anthropic Claude recommended for analysis quality. |
| D3 | News source list | Must be provided by client | System ships with a template; client customizes. |
| D4 | Portfolio input format | CSV / JSON / Excel / manual chat | Support CSV and JSON in MVP; Excel in V2. |
| D5 | Backup frequency | Hourly / daily | Daily recommended; hourly for active trading. |
| D6 | Alert delivery | Chat only / Chat + Email | Chat only in MVP. |
| D7 | Dashboard access | Local only / LAN | Local + LAN (no internet exposure). |

---

## 3. Final Architecture Map

### System Modules

```
┌─────────────────────────────────────────────────────────────────┐
│                    CLIENT INTERFACE LAYER                        │
│  ┌──────────────────────┐  ┌─────────────────────────────────┐  │
│  │  OpenClaw Gateway    │  │  Web Dashboard (port 7777)      │  │
│  │  ├─ Commander Agent  │  │  ├─ Holdings View               │  │
│  │  ├─ Chat Interface   │  │  ├─ Exposure View               │  │
│  │  └─ Alert Delivery   │  │  ├─ Digest View                 │  │
│  └──────────┬───────────┘  │  ├─ Alerts View                 │  │
│             │              │  ├─ Audit Trail                  │  │
│             │              │  └─ System Health                │  │
│             │              └───────────┬─────────────────────┘  │
└─────────────┼──────────────────────────┼────────────────────────┘
              │                          │
              ▼                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                      KLEITOS API (FastAPI)                       │
│  /api/v1/portfolio  /api/v1/events  /api/v1/analysis            │
│  /api/v1/alerts     /api/v1/digests /api/v1/health              │
│  /api/v1/sources    /api/v1/audit   /api/v1/agents              │
└───────────────────────────┬─────────────────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
┌──────────────────┐ ┌───────────┐ ┌──────────────────┐
│   AGENT LAYER    │ │ SCHEDULER │ │  IMPACT ENGINE   │
│ ┌──────────────┐ │ │           │ │ ┌──────────────┐ │
│ │ 1. Intake    │ │ │ Periodic  │ │ │ Rule Engine  │ │
│ │ 2. Classify  │ │ │ Jobs:     │ │ │ LLM Scorer   │ │
│ │ 3. Collect   │ │ │ - Collect │ │ │ Link Mapper  │ │
│ │ 4. Coverage  │ │ │ - Analyze │ │ │ Trace Logger │ │
│ │ 5. Analysis  │ │ │ - Digest  │ │ └──────────────┘ │
│ │ 6. Risk      │ │ │ - Health  │ └──────────────────┘
│ └──────────────┘ │ └───────────┘
└────────┬─────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SHARED FOUNDATIONS                            │
│  ┌────────────┐ ┌──────────────┐ ┌──────────────┐              │
│  │ Portfolio   │ │ Security     │ │ Source       │              │
│  │ Ledger     │ │ Master /     │ │ Registry /   │              │
│  │ (SQLite)   │ │ Classifier   │ │ Allowlist    │              │
│  └────────────┘ └──────────────┘ └──────────────┘              │
│  ┌────────────┐ ┌──────────────┐ ┌──────────────┐              │
│  │ Event      │ │ Impact       │ │ Audit        │              │
│  │ Store +    │ │ Link Map     │ │ Log          │              │
│  │ Link Map   │ │              │ │              │              │
│  └────────────┘ └──────────────┘ └──────────────┘              │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
                    ┌──────────────┐
                    │   SQLite DB  │
                    │ kleitos.db   │
                    │ (WAL mode)   │
                    └──────────────┘
```

### Data Flow

```
Portfolio Input (CSV/JSON/Chat)
        │
        ▼
[1. Intake Agent] ──reconcile──▶ Portfolio Ledger
        │
        ▼
[2. Classification Agent] ──classify──▶ Security Master
        │
        ▼
[3. Collection Agent] ──fetch──▶ Event Store
   (uses Source Registry)         (with Link Map)
        │
        ▼
[4. Coverage QA Agent] ──verify──▶ Coverage Report
        │
        ▼
[Impact Mapping Engine] ──score & link──▶ Impact Records
        │
        ▼
[5. Analysis Agent] ──analyze──▶ Analysis Notes
        │
        ▼
[6. Risk Agent] ──alert──▶ Alerts + Risk Reports
        │
        ▼
[Commander Agent] ──deliver──▶ Client (Chat + Dashboard)
```

---

## 4. Multi-Phase Implementation Roadmap

### Phase 0: Foundation (Week 1-2)

**Goal**: Standing infrastructure — database, API skeleton, project structure, local dev environment.

**Why**: Nothing can be built without the foundation.

**Deliverables**:
- Repository structure with all directories
- SQLite database with schema migrations
- FastAPI application with health endpoint
- Configuration system (settings.yaml, .env)
- Logging framework
- install.sh and setup.sh scripts
- Basic launchd service definitions
- OpenClaw initial setup with Commander workspace

**Dependencies**: None

**Major Tasks**:
- Define and implement full SQLite schema
- Create FastAPI app with CORS, error handling, logging middleware
- Build configuration loader (YAML + env vars)
- Create database migration system
- Write install/setup scripts for macOS
- Set up OpenClaw with initial workspace

**Risks**:
- Schema design mistakes are expensive later
- OpenClaw version compatibility

**Exit Criteria**:
- `install.sh` runs cleanly on Mac mini
- FastAPI serves on port 7777
- SQLite database created with all tables
- Health endpoint returns OK
- OpenClaw gateway starts

**Demoable**: Health endpoint, empty dashboard shell, OpenClaw chat responds

### Phase 1: Portfolio Ledger & Intake (Week 2-3)

**Goal**: Client can input portfolio data and see it stored correctly.

**Why**: The portfolio ledger is the canonical source of truth. Everything depends on it.

**Deliverables**:
- Portfolio Ledger module (CRUD operations)
- Intake Agent (parse CSV/JSON, standardize identifiers, reconcile)
- API endpoints: POST/GET portfolio, holdings, trades
- Audit trail for all ledger changes
- OpenClaw skill for Intake Agent
- Dashboard: Holdings view

**Dependencies**: Phase 0

**Major Tasks**:
- Implement portfolio data models
- Build CSV/JSON parsers with validation
- Implement ticker/ISIN/venue standardization
- Build reconciliation logic with conflict detection
- Create audit logging for every ledger mutation
- Build holdings API endpoints
- Create OpenClaw intake skill

**Risks**:
- Identifier standardization edge cases (multiple share classes, ADRs, etc.)
- CSV format variations

**Exit Criteria**:
- Upload CSV → holdings appear in ledger
- Duplicate upload → reconciliation detects, no data corruption
- Audit trail shows every change
- Client can ask Commander "show my holdings" and get a response

**Demoable**: Upload portfolio CSV, see holdings in dashboard and chat

### Phase 2: Classification & Exposure (Week 3-4)

**Goal**: Every holding is classified and portfolio exposures are visible.

**Why**: Classification drives the entire intelligence pipeline — without it, events cannot be mapped to holdings.

**Deliverables**:
- Security Master module
- Classification Agent (sector, geography, currency, theme tagging)
- Exposure calculation engine
- API endpoints: GET exposures by sector/geography/theme/currency
- Dashboard: Exposure views (sector pie, geography bar, theme matrix)
- OpenClaw skill for Classification Agent

**Dependencies**: Phase 1

**Major Tasks**:
- Build security master data model
- Implement classification logic (rule-based + LLM-assisted)
- Build exposure aggregation queries
- Create exposure API endpoints
- Build dashboard exposure views
- Create OpenClaw classification skill

**Risks**:
- Classification accuracy for unusual securities
- Theme tagging subjectivity

**Exit Criteria**:
- Every holding has sector, geography, currency classification
- Exposure views show correct aggregations
- Client can ask "what is my tech exposure?" and get accurate answer

**Demoable**: Exposure charts in dashboard, chat-based exposure queries

### Phase 3: Source Registry & Collection (Week 4-6)

**Goal**: System fetches news/events from approved sources and stores them.

**Why**: The intelligence pipeline cannot function without data.

**Deliverables**:
- Source Registry with allowlist management
- Collection Agent with parser framework
- Event Store with deduplication
- Event-to-holding link mapping
- API endpoints: GET events, sources, coverage
- Scheduler: periodic collection jobs
- OpenClaw skill for Collection Agent
- Dashboard: Recent events view

**Dependencies**: Phase 2

**Major Tasks**:
- Build source registry with YAML configuration
- Implement HTTP fetcher with rate limiting and retry
- Build parser adapter framework (RSS, API, HTML scraping)
- Implement 5-10 initial source parsers
- Build event deduplication logic
- Implement event-to-holding mapping (by ticker, sector, geography)
- Set up APScheduler for periodic collection
- Build event API endpoints

**Risks**:
- Parser brittleness (HTML structure changes)
- Rate limiting from sources
- False positive event-holding mapping

**Exit Criteria**:
- Scheduler runs collection every 30 minutes
- Events appear in event store with correct holding links
- Source health is monitored
- Client can ask "any news on [TICKER]?" and get results

**Demoable**: Live news feed in dashboard, chat-based news queries

### Phase 4: Impact Mapping & Analysis (Week 6-8)

**Goal**: Events are scored for relevance and analyzed for portfolio impact.

**Why**: This is the core intelligence — transforming raw events into actionable insights.

**Deliverables**:
- Impact Mapping Engine (rule-based + LLM scoring)
- Coverage QA Agent
- Portfolio Analysis Agent
- Event classification (type, scope, channel, direction, horizon, materiality, confidence)
- Analysis notes with source traces
- API endpoints: GET analysis, impact scores
- OpenClaw skills for Coverage QA and Analysis
- Dashboard: Analysis view, digest view

**Dependencies**: Phase 3

**Major Tasks**:
- Build impact mapping rule engine
- Implement LLM-based relevance scoring
- Build coverage gap detection
- Implement analysis agent with structured output
- Create explanation trace storage
- Build daily digest generator
- Create analysis API endpoints

**Risks**:
- False positives in relevance scoring
- LLM hallucination in analysis
- Coverage gaps not detected

**Exit Criteria**:
- Every event has materiality score
- Analysis notes explain impact with source traces
- Coverage QA flags gaps
- Daily digest is generated
- Client receives organized morning briefing

**Demoable**: Daily digest in chat, analysis notes in dashboard

### Phase 5: Risk, Alerts & Full Command Center (Week 8-10)

**Goal**: Complete portfolio intelligence system with risk monitoring and polished client experience.

**Why**: Risk monitoring is the safety net. The Command Center is what the client actually uses.

**Deliverables**:
- Risk & Alerting Agent
- Alert delivery via OpenClaw
- Full Command Center (chat + dashboard)
- Concentration risk monitoring
- Calendar event clustering
- Thesis drift detection
- Dashboard: Alerts view, full audit trail, system health
- OpenClaw skill for Risk Agent
- Commander Agent fully orchestrated

**Dependencies**: Phase 4

**Major Tasks**:
- Build risk calculation engine
- Implement concentration alerts (by name, sector, geography, currency)
- Build calendar cluster detection
- Implement thesis drift heuristics
- Build alert delivery through OpenClaw
- Polish Commander Agent orchestration
- Complete dashboard views
- End-to-end testing

**Risks**:
- Noisy alerts (too many false alarms)
- Alert fatigue
- Commander Agent coordination complexity

**Exit Criteria**:
- Risk alerts fire correctly
- Client receives material alerts promptly
- Full Command Center is operational
- All 6 agents + Commander work end-to-end
- System is client-ready

**Demoable**: Full system demo — upload portfolio, see classifications, receive digest, get alerts

---

## 5. Recommended Implementation Order

Build order optimized for **earliest usability** while maintaining **correctness**:

```
Week 1:  Schema + API skeleton + install scripts + OpenClaw base setup
Week 2:  Portfolio Ledger + Intake Agent + Holdings API + Holdings view
Week 3:  Security Master + Classification Agent + Exposure views
Week 4:  Source Registry + Collection Agent skeleton + first 3 parsers
Week 5:  Event Store + Link Map + more parsers + scheduler
Week 6:  Impact Mapping Engine (rules) + Coverage QA Agent
Week 7:  Impact Mapping Engine (LLM scoring) + Analysis Agent
Week 8:  Digest generator + Daily briefing via OpenClaw
Week 9:  Risk Agent + Alerts + Concentration monitoring
Week 10: Commander orchestration + Dashboard polish + end-to-end testing
Week 11: Operational hardening, backup/restore, monitoring
Week 12: Client installation, documentation, handoff preparation
```

**Key principle**: The system becomes minimally useful at Week 3 (client can see their portfolio and exposures) and progressively more intelligent through Week 10.

---

## 6. Data Model / Schema Plan

### Core Entities

All data lives in a single SQLite database (`kleitos.db`) with WAL mode enabled for concurrent read access.

#### Canonical Tables (Source of Truth)

```sql
-- Portfolio Ledger
holdings (
    id              TEXT PRIMARY KEY,       -- UUID
    ticker          TEXT NOT NULL,
    isin            TEXT,
    venue           TEXT,                   -- exchange/market
    currency        TEXT NOT NULL,
    quantity         REAL NOT NULL,
    avg_cost_basis   REAL,
    current_price    REAL,
    market_value     REAL,
    weight_pct       REAL,
    portfolio_id     TEXT NOT NULL DEFAULT 'main',
    status           TEXT NOT NULL DEFAULT 'active',  -- active/closed
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

trades (
    id              TEXT PRIMARY KEY,
    holding_id      TEXT REFERENCES holdings(id),
    ticker          TEXT NOT NULL,
    trade_type      TEXT NOT NULL,          -- buy/sell/dividend/split/etc
    quantity        REAL NOT NULL,
    price           REAL,
    currency        TEXT,
    trade_date      TEXT NOT NULL,
    settlement_date TEXT,
    notes           TEXT,
    source          TEXT,                   -- manual/csv/api
    created_at      TEXT NOT NULL
);

-- Security Master
securities (
    id              TEXT PRIMARY KEY,
    ticker          TEXT NOT NULL,
    isin            TEXT,
    name            TEXT,
    venue           TEXT,
    currency        TEXT NOT NULL,
    issuer          TEXT,
    sector          TEXT,
    subsector       TEXT,
    industry        TEXT,
    geography       TEXT,                   -- country of primary exposure
    domicile        TEXT,                   -- country of incorporation
    market_cap_bucket TEXT,                 -- mega/large/mid/small/micro
    themes          TEXT,                   -- JSON array of theme tags
    classification_source TEXT,             -- manual/llm/data-provider
    classification_confidence TEXT,         -- high/medium/low
    classified_at   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Source Registry
sources (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    domain          TEXT NOT NULL,
    source_type     TEXT NOT NULL,          -- rss/api/scrape
    parser_id       TEXT NOT NULL,
    priority        INTEGER DEFAULT 5,
    trust_level     TEXT DEFAULT 'standard', -- premium/standard/low
    enabled         INTEGER DEFAULT 1,
    rate_limit_rpm  INTEGER DEFAULT 10,
    requires_auth   INTEGER DEFAULT 0,
    auth_type       TEXT,                   -- api_key/oauth/cookie
    last_fetched_at TEXT,
    last_status     TEXT,
    created_at      TEXT NOT NULL
);
```

#### Event Tables (Timestamped, Immutable)

```sql
events (
    id              TEXT PRIMARY KEY,
    source_id       TEXT REFERENCES sources(id),
    external_id     TEXT,                   -- source's own ID/URL
    title           TEXT NOT NULL,
    summary         TEXT,
    content         TEXT,
    url             TEXT,
    published_at    TEXT,
    fetched_at      TEXT NOT NULL,
    event_type      TEXT,                   -- earnings/dividend/analyst/macro/etc
    scope           TEXT,                   -- stock/peer/sector/geography/theme/market
    direction       TEXT,                   -- positive/negative/mixed/unclear
    horizon         TEXT,                   -- immediate/near/medium/long
    materiality     TEXT DEFAULT 'unscored', -- immaterial/watch/important/critical
    confidence      TEXT DEFAULT 'unscored', -- low/medium/high
    dedup_hash      TEXT UNIQUE,            -- for deduplication
    raw_data        TEXT,                   -- original JSON/HTML preserved
    created_at      TEXT NOT NULL
);

event_links (
    id              TEXT PRIMARY KEY,
    event_id        TEXT NOT NULL REFERENCES events(id),
    link_type       TEXT NOT NULL,          -- holding/sector/geography/theme/currency/market
    link_target     TEXT NOT NULL,          -- ticker, sector name, country, theme, etc
    relevance_score REAL,                   -- 0.0 to 1.0
    impact_channel  TEXT,                   -- revenue/margins/regulation/fx/sentiment/etc
    link_source     TEXT,                   -- rules/llm/manual
    created_at      TEXT NOT NULL
);
```

#### Derived / Analysis Tables

```sql
analysis_notes (
    id              TEXT PRIMARY KEY,
    event_id        TEXT REFERENCES events(id),
    holding_id      TEXT REFERENCES holdings(id),
    note_type       TEXT NOT NULL,          -- impact/thesis/risk/summary
    content         TEXT NOT NULL,
    materiality     TEXT,
    confidence      TEXT,
    agent_id        TEXT NOT NULL,          -- which agent produced this
    model_id        TEXT,                   -- which LLM model
    prompt_hash     TEXT,                   -- hash of prompt used (for reproducibility)
    source_trace    TEXT,                   -- JSON: list of source references
    created_at      TEXT NOT NULL
);

alerts (
    id              TEXT PRIMARY KEY,
    alert_type      TEXT NOT NULL,          -- concentration/calendar/thesis_drift/material_event/coverage_gap
    severity        TEXT NOT NULL,          -- info/warning/critical
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    related_holdings TEXT,                  -- JSON array of holding IDs
    related_events  TEXT,                   -- JSON array of event IDs
    acknowledged    INTEGER DEFAULT 0,
    acknowledged_at TEXT,
    delivered       INTEGER DEFAULT 0,
    delivered_at    TEXT,
    agent_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

digests (
    id              TEXT PRIMARY KEY,
    digest_type     TEXT NOT NULL,          -- daily/weekly/ad_hoc
    period_start    TEXT NOT NULL,
    period_end      TEXT NOT NULL,
    content         TEXT NOT NULL,          -- structured JSON or markdown
    event_count     INTEGER,
    alert_count     INTEGER,
    holding_count   INTEGER,
    delivered       INTEGER DEFAULT 0,
    delivered_at    TEXT,
    created_at      TEXT NOT NULL
);
```

#### Audit & System Tables

```sql
audit_log (
    id              TEXT PRIMARY KEY,
    entity_type     TEXT NOT NULL,          -- holding/trade/event/classification/etc
    entity_id       TEXT NOT NULL,
    action          TEXT NOT NULL,          -- create/update/delete
    old_value       TEXT,                   -- JSON
    new_value       TEXT,                   -- JSON
    agent_id        TEXT,
    user_id         TEXT DEFAULT 'operator',
    reason          TEXT,
    created_at      TEXT NOT NULL
);

agent_runs (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    run_type        TEXT NOT NULL,          -- scheduled/manual/triggered
    status          TEXT NOT NULL,          -- running/completed/failed
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    items_processed INTEGER DEFAULT 0,
    items_failed    INTEGER DEFAULT 0,
    error_message   TEXT,
    duration_ms     INTEGER
);

coverage_reports (
    id              TEXT PRIMARY KEY,
    holding_id      TEXT REFERENCES holdings(id),
    ticker          TEXT NOT NULL,
    has_recent_earnings INTEGER DEFAULT 0,
    has_recent_dividend INTEGER DEFAULT 0,
    has_recent_analyst  INTEGER DEFAULT 0,
    has_recent_news     INTEGER DEFAULT 0,
    last_event_at   TEXT,
    gap_days        INTEGER,
    flag            TEXT,                   -- ok/warning/gap
    checked_at      TEXT NOT NULL
);

system_health (
    id              TEXT PRIMARY KEY,
    component       TEXT NOT NULL,          -- api/scheduler/openclaw/db
    status          TEXT NOT NULL,          -- ok/degraded/down
    message         TEXT,
    checked_at      TEXT NOT NULL
);
```

### Data Design Principles

| Principle | Implementation |
|-----------|---------------|
| Canonical vs Derived | `holdings`, `trades`, `securities`, `sources` are canonical. `analysis_notes`, `alerts`, `digests` are derived. |
| Versioned | Audit log captures every change to canonical data. |
| Audited | Every mutation writes to `audit_log`. |
| Replayable | Events are immutable once stored. Analysis can be re-run. |
| Local storage | Single SQLite file at `~/kleitos-data/db/kleitos.db`. |

---

## 7. Agent Contracts

### Agent 1: Intake & Reconciliation

| Aspect | Specification |
|--------|---------------|
| **Inputs** | CSV files, JSON files, manual text input (via chat) containing holdings, trades, dividends, orders |
| **Outputs** | Validated and reconciled records written to Portfolio Ledger; reconciliation report; list of flagged inconsistencies |
| **Read Permissions** | `holdings`, `trades`, `securities` (for identifier lookup) |
| **Write Permissions** | `holdings`, `trades`, `audit_log`, `agent_runs` |
| **Failure Behavior** | On parse error: reject entire file, report specific errors. On reconciliation conflict: flag and hold, do not overwrite. On network error: N/A (no external calls). |
| **Retry Behavior** | No retries for parse errors (human must fix). Retry DB writes up to 3 times with backoff. |
| **Logging** | Log every file received, every record parsed, every reconciliation decision, every conflict. |
| **Must Never** | Guess missing data. Overwrite existing records without flagging conflicts. Make external network calls. Access source registry or event store. |

### Agent 2: Classification & Exposure

| Aspect | Specification |
|--------|---------------|
| **Inputs** | List of holdings from Portfolio Ledger that need classification or re-classification |
| **Outputs** | Updated `securities` records with sector/geography/theme classifications; exposure aggregation reports |
| **Read Permissions** | `holdings`, `securities`, `trades` |
| **Write Permissions** | `securities`, `audit_log`, `agent_runs` |
| **Failure Behavior** | On LLM failure: use cached classification if available, flag as stale. On unknown security: flag for manual review, do not guess. |
| **Retry Behavior** | Retry LLM calls up to 3 times with exponential backoff. |
| **Logging** | Log every classification decision with confidence level and source (manual/LLM/data). |
| **Must Never** | Modify holdings or trades. Access event store. Make unauthorized external calls. Classify with low confidence without flagging. |

### Agent 3: News & Event Collection

| Aspect | Specification |
|--------|---------------|
| **Inputs** | Portfolio holdings list (tickers, sectors, geographies); Source Registry (approved sources only) |
| **Outputs** | Deduplicated events written to Event Store; event-to-holding links written to `event_links`; source health status |
| **Read Permissions** | `holdings`, `securities`, `sources` |
| **Write Permissions** | `events`, `event_links`, `sources` (last_fetched_at, last_status only), `agent_runs` |
| **Failure Behavior** | On source failure: log error, update source status, continue with other sources. On parse failure: store raw data, flag for review. On rate limit: back off and retry. |
| **Retry Behavior** | Retry HTTP requests up to 3 times with exponential backoff. Respect source-specific rate limits. |
| **Logging** | Log every source fetch attempt, every event stored, every dedup decision, every link created. |
| **Must Never** | Fetch from sources not in the Source Registry. Bypass rate limits. Modify holdings or securities. Store events without dedup check. Access external URLs not in allowlist. |

### Agent 4: Coverage QA / Completeness

| Aspect | Specification |
|--------|---------------|
| **Inputs** | Complete holdings list; recent events from Event Store; expected event calendar (earnings dates, ex-div dates) |
| **Outputs** | Coverage report per holding; gap alerts; quality metrics |
| **Read Permissions** | `holdings`, `securities`, `events`, `event_links`, `coverage_reports` |
| **Write Permissions** | `coverage_reports`, `alerts`, `agent_runs` |
| **Failure Behavior** | On incomplete data: report what is checkable, flag what is not. Never claim coverage is complete if it cannot be verified. |
| **Retry Behavior** | No external calls to retry. Retry DB queries up to 3 times. |
| **Logging** | Log every holding checked, every gap detected, every coverage assessment. |
| **Must Never** | Modify holdings, securities, events, or sources. Fetch external data. Mark gaps as covered. |

### Agent 5: Portfolio Analysis

| Aspect | Specification |
|--------|---------------|
| **Inputs** | Quality-checked events with impact scores; holdings with classifications; current exposures |
| **Outputs** | Analysis notes explaining impact of events on holdings/sectors/portfolio; materiality assessments; digest contributions |
| **Read Permissions** | `holdings`, `securities`, `events`, `event_links`, `coverage_reports`, `analysis_notes` (previous) |
| **Write Permissions** | `analysis_notes`, `digests`, `agent_runs` |
| **Failure Behavior** | On LLM failure: do not produce analysis, log failure, flag for retry. Never produce analysis without adequate source data. |
| **Retry Behavior** | Retry LLM calls up to 3 times. On persistent failure, produce a "analysis pending" placeholder. |
| **Logging** | Log every analysis produced, the model used, the prompt hash, all source references. |
| **Must Never** | Modify holdings, trades, events, or classifications. Make investment recommendations. State opinions as facts. Produce analysis without source traces. Access external sources directly. |

### Agent 6: Risk & Alerting

| Aspect | Specification |
|--------|---------------|
| **Inputs** | Current portfolio state (holdings, exposures, classifications); recent events and analysis; risk parameters |
| **Outputs** | Risk assessments; concentration alerts; calendar cluster alerts; thesis drift warnings; material event alerts |
| **Read Permissions** | `holdings`, `securities`, `events`, `event_links`, `analysis_notes`, `alerts` (previous) |
| **Write Permissions** | `alerts`, `agent_runs` |
| **Failure Behavior** | On calculation error: log and flag, do not suppress. On threshold ambiguity: alert with low severity rather than not alerting. |
| **Retry Behavior** | No external calls. Retry DB operations up to 3 times. |
| **Logging** | Log every risk calculation, every alert generated, every threshold evaluation. |
| **Must Never** | Modify holdings, trades, events, or classifications. Suppress alerts. Recommend trades. Change risk parameters without audit trail. |

---

## 8. Impact Mapping Engine Design

### Architecture

The Impact Mapping Engine is the analytical core that determines which events affect which parts of the portfolio and why. It operates in two stages:

**Stage 1: Rule-Based Mapping (deterministic, fast)**

```
Event → Rule Engine → Candidate Links
```

Rules are organized by matching strategy:

| Rule Type | Logic | Example |
|-----------|-------|---------|
| Ticker Match | Event mentions a ticker in the portfolio | "AAPL reports Q3 earnings" → AAPL holding |
| ISIN Match | Event contains an ISIN in the portfolio | Regulatory filing with ISIN |
| Company Name Match | Event mentions issuer name (fuzzy) | "Apple Inc announces..." → AAPL |
| Sector Match | Event topic matches sector classification | "Semiconductor shortage" → all semis holdings |
| Geography Match | Event concerns a country where holdings operate | "EU regulation on..." → EU-exposed holdings |
| Theme Match | Event topic matches theme tags | "AI regulation" → AI-themed holdings |
| Currency Match | Event affects a currency in the portfolio | "EUR/USD drops" → EUR-denominated holdings |
| Market-Wide Match | Event is systemic (rate changes, crashes, etc.) | "Fed raises rates" → all holdings |
| Peer Group Match | Event affects named competitors | "Samsung recall" → tech hardware peers |

**Stage 2: LLM-Assisted Scoring (nuanced, slower)**

```
Candidate Links → LLM Scorer → Scored & Classified Links
```

For each candidate link, the LLM evaluates:

1. **Relevance Score** (0.0 - 1.0): How directly does this event affect this holding?
2. **Impact Channel**: Through what mechanism (revenue, margins, regulation, sentiment, etc.)?
3. **Direction**: Positive, negative, mixed, or unclear?
4. **Horizon**: Immediate, near-term, medium-term, or long-term?
5. **Materiality**: Immaterial, watch, important, or critical?
6. **Confidence**: How confident is the assessment?
7. **Explanation**: Plain-English explanation of the impact logic.

### Scope Classification Logic

```python
# Pseudocode for scope determination
def classify_scope(event, portfolio):
    affected_holdings = rule_engine.find_matches(event, portfolio)

    if len(affected_holdings) == 0:
        return "unrelated"
    elif len(affected_holdings) == 1:
        return "single_stock"
    elif all_same_subsector(affected_holdings):
        return "peer_group"
    elif all_same_sector(affected_holdings):
        return "sector"
    elif all_same_geography(affected_holdings):
        return "geography"
    elif all_same_theme(affected_holdings):
        return "theme"
    elif all_same_currency(affected_holdings):
        return "currency"
    elif len(affected_holdings) > portfolio_size * 0.5:
        return "systemic"
    else:
        return "multi_factor"  # crosses multiple dimensions
```

### Controlling False Positives / False Negatives

| Control | Implementation |
|---------|---------------|
| Minimum relevance threshold | Links below 0.3 relevance are discarded |
| Confidence gating | Low-confidence links are flagged, not promoted to alerts |
| Human review queue | Ambiguous mappings are held for operator review |
| Feedback loop | Operator can confirm/reject links, improving future scoring |
| Rule auditing | Every rule-based match is logged with the rule ID |
| LLM audit | Every LLM scoring call is logged with prompt hash and response |
| Dedup protection | Same event+holding link is never created twice |
| Staleness check | Old events (>7 days) are not re-scored unless explicitly requested |

### Which Parts Are Rules-Based vs Model-Based

| Component | Type | Rationale |
|-----------|------|-----------|
| Ticker/ISIN/Name matching | Rules | Deterministic, must be exact |
| Sector/Geography/Theme matching | Rules | Based on classification data, deterministic |
| Currency matching | Rules | Based on holding currency, deterministic |
| Market-wide detection | Rules + heuristics | Keyword lists + event type |
| Relevance scoring | LLM | Requires context understanding |
| Impact channel identification | LLM | Requires reasoning about causal chains |
| Materiality assessment | LLM + rules | LLM provides initial, rules enforce thresholds |
| Explanation generation | LLM | Requires natural language reasoning |
| Direction assessment | LLM | Requires understanding nuance |

### Explanation Trace Storage

Every impact mapping produces an audit trace stored as JSON:

```json
{
  "event_id": "evt_123",
  "holding_id": "hld_456",
  "trace": {
    "rule_matches": [
      {"rule": "ticker_match", "matched": "AAPL", "confidence": 1.0}
    ],
    "llm_scoring": {
      "model": "claude-sonnet-4-6",
      "prompt_hash": "abc123",
      "relevance": 0.85,
      "impact_channel": "revenue",
      "direction": "negative",
      "materiality": "important",
      "confidence": "high",
      "explanation": "Apple's key supplier reported...",
      "timestamp": "2026-03-12T10:30:00Z"
    },
    "final_decision": {
      "linked": true,
      "scope": "peer_group",
      "materiality": "important"
    }
  }
}
```

---

## 9. OpenClaw Multi-Agent Command Center Design

### Overview

The Command Center is the client's primary interface, built on top of OpenClaw. It combines:

1. **Chat Interface** (via OpenClaw channels: Telegram/Signal/WebChat)
2. **Web Dashboard** (served by Kleitos API at `http://localhost:7777`)

The chat is for interaction, questions, alerts, and digests.
The dashboard is for visual exploration, structured data, and audit trails.

### Multi-Agent Architecture in OpenClaw

```
~/.openclaw/
├── openclaw.json                    # Multi-agent routing config
├── workspace-commander/             # Commander Agent (client-facing)
│   ├── AGENTS.md
│   ├── SOUL.md
│   ├── USER.md
│   ├── IDENTITY.md
│   └── skills/
│       ├── portfolio-query/SKILL.md
│       ├── news-query/SKILL.md
│       ├── exposure-query/SKILL.md
│       ├── digest-request/SKILL.md
│       ├── alert-review/SKILL.md
│       └── analysis-request/SKILL.md
├── workspace-intake/
│   ├── AGENTS.md
│   └── skills/
│       └── intake-process/SKILL.md
├── workspace-classification/
│   ├── AGENTS.md
│   └── skills/
│       └── classify-holdings/SKILL.md
├── workspace-collection/
│   ├── AGENTS.md
│   └── skills/
│       └── collect-news/SKILL.md
├── workspace-coverage/
│   ├── AGENTS.md
│   └── skills/
│       └── check-coverage/SKILL.md
├── workspace-analysis/
│   ├── AGENTS.md
│   └── skills/
│       └── analyze-events/SKILL.md
└── workspace-risk/
    ├── AGENTS.md
    └── skills/
        └── assess-risk/SKILL.md
```

### Commander Agent Behavior

The Commander is the client's conversational partner. It:

1. **Receives client messages** via the configured channel
2. **Understands intent** (query, request, follow-up)
3. **Delegates to specialists** by calling the Kleitos API
4. **Formats responses** in a clear, organized way
5. **Delivers scheduled updates** (morning digest, alerts)

Example interactions:

| Client Says | Commander Does |
|-------------|---------------|
| "Show my portfolio" | Calls `/api/v1/portfolio/holdings`, formats response |
| "What's my tech exposure?" | Calls `/api/v1/portfolio/exposure?dimension=sector&filter=technology` |
| "Any news on AAPL?" | Calls `/api/v1/events?ticker=AAPL&days=7`, summarizes |
| "Why does that matter?" | Calls `/api/v1/analysis?event_id=X`, explains impact |
| "Morning briefing" | Calls `/api/v1/digests/latest`, delivers formatted digest |
| "Any alerts?" | Calls `/api/v1/alerts?unacknowledged=true`, lists alerts |
| "Upload this CSV" | Triggers Intake Agent via `/api/v1/intake/upload` |
| "How healthy is the system?" | Calls `/api/v1/health`, reports status |

### Message Organization Model

Responses are structured, not raw dumps:

```
📊 Morning Digest — March 12, 2026

MATERIAL DEVELOPMENTS (2)
━━━━━━━━━━━━━━━━━━━━━
[CRITICAL] TSMC Q1 guidance miss
  → Affects: AAPL, NVDA, AMD (supply chain)
  → Impact: Near-term margin pressure
  → Source: Company filing, Reuters

[IMPORTANT] EU Digital Markets Act enforcement
  → Affects: GOOGL, META, AMZN (regulation)
  → Impact: Compliance costs, potential fines
  → Source: EU Commission press release

WATCH LIST (3)
━━━━━━━━━━━━━
• USD/JPY at 12-month low — affects JPY-revenue names
• Oil inventory data mixed — energy sector neutral
• Fed speaker hawkish — bond proxy names may lag

PORTFOLIO SNAPSHOT
━━━━━━━━━━━━━━━━━
Holdings: 47 | Sectors: 8 | Top weight: AAPL (6.2%)
Unacknowledged alerts: 2

Type "details [topic]" for more, or "alerts" to review.
```

### Dashboard Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  KLEITOS — Portfolio Intelligence Command Center                │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  [Holdings] [Exposures] [Digest] [Alerts] [Audit] [Health]  │
│  └──────────────────────────────────────────────────────────┘   │
├─────────────────────────────┬───────────────────────────────────┤
│                             │                                   │
│  MAIN CONTENT AREA          │  SIDEBAR                          │
│                             │                                   │
│  (Changes based on tab)     │  Recent Alerts (3)                │
│                             │  ┌─────────────────────────┐      │
│  Holdings Tab:              │  │ ⚠ TSMC guidance miss   │      │
│  - Sortable table           │  │ ⚠ EU DMA enforcement   │      │
│  - Search/filter            │  │ ℹ USD/JPY weakness     │      │
│  - Per-holding details      │  └─────────────────────────┘      │
│                             │                                   │
│  Exposures Tab:             │  System Status                    │
│  - Sector pie chart         │  ┌─────────────────────────┐      │
│  - Geography bar chart      │  │ API: ● OK               │      │
│  - Theme matrix             │  │ Scheduler: ● OK         │      │
│  - Currency breakdown       │  │ OpenClaw: ● OK          │      │
│                             │  │ Last collection: 10m ago│      │
│  Digest Tab:                │  │ Next collection: 20m    │      │
│  - Latest digest            │  └─────────────────────────┘      │
│  - Historical digests       │                                   │
│                             │  Quick Actions                    │
│  Alerts Tab:                │  ┌─────────────────────────┐      │
│  - Active alerts            │  │ [Run Collection Now]    │      │
│  - Alert history            │  │ [Generate Digest]       │      │
│  - Acknowledge controls     │  │ [Upload Portfolio]      │      │
│                             │  │ [Check Health]          │      │
│  Audit Tab:                 │  └─────────────────────────┘      │
│  - Full audit trail         │                                   │
│  - Filterable by entity     │                                   │
│                             │                                   │
│  Health Tab:                │                                   │
│  - Agent run history        │                                   │
│  - Source health status     │                                   │
│  - Error log                │                                   │
└─────────────────────────────┴───────────────────────────────────┘
```

### How It Fits With OpenClaw

- OpenClaw Gateway runs as a separate process on the Mac mini
- The Commander Agent workspace contains skills that make HTTP calls to the Kleitos API
- OpenClaw handles the messaging channel adapters (Telegram, Signal, etc.)
- The Kleitos web dashboard is independent of OpenClaw (direct browser access)
- Both are part of the "Command Center" — one is chat-based, one is visual
- The Commander Agent can include dashboard links in responses: "See details: http://localhost:7777/holdings/AAPL"

---

## 10. Source Control / Registry Design

### Source Registration

Sources are defined in `config/sources.yaml`:

```yaml
sources:
  - id: reuters-rss
    name: Reuters Business News
    domain: reuters.com
    type: rss
    url: https://www.reuters.com/business/rss
    parser: rss_generic
    priority: 1
    trust_level: premium
    enabled: true
    rate_limit_rpm: 5
    requires_auth: false
    tags: [general, macro, corporate]

  - id: sec-edgar
    name: SEC EDGAR Filings
    domain: sec.gov
    type: api
    url: https://efts.sec.gov/LATEST/search-index
    parser: sec_edgar
    priority: 1
    trust_level: premium
    enabled: true
    rate_limit_rpm: 10
    requires_auth: false
    tags: [filings, regulatory, us]
```

### Allowlisting

- **Only sources in `config/sources.yaml` can be fetched**
- The Collection Agent validates every URL against the source registry before fetching
- If a URL's domain is not in the registry, it is rejected and logged
- Adding a new source requires editing `config/sources.yaml` and restarting the scheduler
- Source changes are logged in the audit trail

### Parser Adapter System

```
src/sources/parsers/
├── __init__.py
├── base.py              # Abstract parser interface
├── rss_generic.py       # Generic RSS/Atom parser
├── sec_edgar.py         # SEC EDGAR filing parser
├── newsapi.py           # NewsAPI.org parser
├── finnhub.py           # Finnhub API parser
├── yahoo_finance.py     # Yahoo Finance parser
├── custom_html.py       # Configurable HTML scraper
└── csv_feed.py          # CSV/TSV feed parser
```

Each parser implements:
```python
class BaseParser:
    def fetch(self, source_config) -> list[RawEvent]
    def parse(self, raw_data) -> list[ParsedEvent]
    def validate(self, event) -> bool
    def health_check(self, source_config) -> SourceStatus
```

### Parser Drift Detection

- Each parser stores a hash of the expected response structure
- If the structure changes significantly (>30% field mismatch), the parser flags a drift warning
- Drift warnings become alerts after 3 consecutive occurrences
- Source health dashboard shows drift status

### Coverage Gap Identification

- Coverage QA Agent runs after each collection cycle
- For each holding, checks if key event types are present within expected timeframes
- Expected events: earnings (quarterly), dividends (per schedule), analyst coverage (monthly minimum)
- Gaps are flagged as alerts with severity based on gap duration

### Preventing Unauthorized Source Use

- Source registry is the single allowlist — no other mechanism can add sources
- Collection Agent code-level check: `assert url_domain in approved_domains`
- Audit log records every fetch attempt, including rejected ones
- No general web browsing capability — only registered source endpoints

---

## 11. Security and Permission Model

### Isolation Boundaries

```
┌─────────────────────────────────────────────┐
│ macOS User Account                           │
│                                              │
│  ┌──────────────────────┐                    │
│  │ Kleitos Core Process │ Port 7777          │
│  │ (Python/FastAPI)     │ Bind: 127.0.0.1    │
│  │                      │ (LAN optional)      │
│  │ Owns: ~/kleitos-data/│                    │
│  └──────────────────────┘                    │
│                                              │
│  ┌──────────────────────┐                    │
│  │ OpenClaw Gateway     │ Port 3000          │
│  │ (Node.js)            │ Bind: 127.0.0.1    │
│  │                      │                    │
│  │ Owns: ~/.openclaw/   │                    │
│  └──────────────────────┘                    │
│                                              │
│  ┌──────────────────────┐                    │
│  │ SQLite DB            │                    │
│  │ ~/kleitos-data/db/   │                    │
│  │ kleitos.db           │                    │
│  │ (file permissions:   │                    │
│  │  owner read/write)   │                    │
│  └──────────────────────┘                    │
└─────────────────────────────────────────────┘
```

### Secret Management

| Secret | Storage | Access |
|--------|---------|--------|
| Anthropic API Key | `~/.kleitos.env` (file mode 600) | Kleitos Core, OpenClaw |
| Source API Keys | `~/.kleitos.env` (file mode 600) | Kleitos Core only |
| Telegram Bot Token | OpenClaw config (`~/.openclaw/openclaw.json`) | OpenClaw only |
| Database | SQLite file (file mode 600) | Kleitos Core only |

- `.env` files are never committed to version control
- Template `.env.template` shows required variables without values
- On install, `setup.sh` creates `.env` with correct permissions

### Per-Agent Permissions (enforced in code)

| Agent | Reads | Writes | External |
|-------|-------|--------|----------|
| Intake | holdings, trades, securities | holdings, trades, audit | None |
| Classification | holdings, securities | securities, audit | Anthropic API (for LLM classification) |
| Collection | holdings, securities, sources | events, event_links, sources (status) | Registered sources only |
| Coverage QA | holdings, securities, events, coverage | coverage_reports, alerts | None |
| Analysis | holdings, securities, events, event_links, coverage | analysis_notes, digests | Anthropic API |
| Risk | holdings, securities, events, analysis, alerts | alerts | None |
| Commander | All (read only) | None (delegates) | Anthropic API (via OpenClaw) |

### Audit Logging

- Every data mutation is logged in `audit_log` table
- Logs include: who (agent), what (entity+action), when, old value, new value
- Audit log is append-only — no deletes, no updates
- Audit log can be exported for review

### Tamper Resistance

- Database file is owned by the running user with mode 600
- Audit log table uses a rolling hash chain (each entry includes hash of previous entry)
- Application-level write checks enforce agent permissions
- No direct database access is exposed to OpenClaw — all through API

---

## 12. Mac Mini Deployment and Operations Design

### Recommended Runtime Approach

**Native Python processes managed by macOS `launchd`** — no Docker.

Rationale:
- Docker on macOS runs in a Linux VM, adding overhead and complexity
- Native processes use less memory and CPU
- `launchd` is the native macOS service manager — reliable, well-understood, survives reboots
- Python venv is simple to create and update
- No container networking complexity
- Easier to troubleshoot

### Service Definitions

Two `launchd` services:

**1. Kleitos Core** (`com.kleitos.core.plist`)
- Runs: FastAPI app + APScheduler
- Port: 7777
- Auto-starts on boot
- Auto-restarts on crash (with backoff)
- Logs to `~/kleitos-data/logs/`

**2. OpenClaw Gateway** (`com.kleitos.openclaw.plist`)
- Runs: OpenClaw gateway
- Auto-starts on boot
- Auto-restarts on crash
- Logs to `~/kleitos-data/logs/openclaw/`

### Directory Layout on Mac Mini

```
~/
├── kleitos/                    # Application code
│   ├── src/                    # Python source
│   ├── dashboard/              # Web UI
│   ├── config/                 # Configuration
│   ├── start.sh
│   ├── stop.sh
│   ├── status.sh
│   ├── healthcheck.sh
│   └── update.sh
├── kleitos-data/               # Runtime data (survives updates)
│   ├── db/
│   │   └── kleitos.db
│   ├── logs/
│   │   ├── kleitos.log
│   │   ├── kleitos-error.log
│   │   └── openclaw/
│   ├── backups/
│   │   └── kleitos-2026-03-12.db
│   └── exports/
├── .kleitos.env                # Secrets (mode 600)
└── .openclaw/                  # OpenClaw config
    ├── openclaw.json
    └── workspaces/
```

### Startup on Boot

`launchd` plists in `~/Library/LaunchAgents/` ensure both services start on login:

```xml
<!-- com.kleitos.core.plist -->
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>ThrottleInterval</key><integer>10</integer>
```

### Restart Strategy

- `launchd` `KeepAlive=true` ensures automatic restart on crash
- `ThrottleInterval=10` prevents restart loops (waits 10 seconds)
- `healthcheck.sh` can be run manually or via cron to verify
- `status.sh` shows current state of all components

### Monitoring

- `healthcheck.sh` checks: API responding, scheduler running, OpenClaw connected, DB accessible, last collection < 1 hour ago
- Health status is persisted to `system_health` table
- Dashboard health tab shows last 24 hours of health checks
- OpenClaw Commander reports health when asked

### Backup Strategy

- Daily automatic backup: `cp kleitos.db kleitos-$(date).db`
- Keep last 7 daily backups
- Backup runs as a scheduled job in APScheduler
- Manual backup: `./scripts/backup.sh`
- Restore: `./scripts/restore.sh [backup-file]`

### Update Strategy

```bash
./update.sh
# 1. Stops services
# 2. Backs up database
# 3. Pulls latest code (git pull or download)
# 4. Updates Python dependencies
# 5. Runs database migrations
# 6. Restarts services
# 7. Runs health check
```

### Tradeoffs: Docker vs Native

| Factor | Docker | Native (chosen) |
|--------|--------|-----------------|
| Installation complexity | Higher (Docker Desktop) | Lower (brew + pip) |
| Memory overhead | ~2GB for Docker VM | None |
| Startup time | Slower | Faster |
| Debugging | Harder (inside container) | Easier (direct process) |
| Updates | Rebuild image | pip install + restart |
| macOS integration | Poor (VM layer) | Native |
| Reliability | Good (but VM can crash) | Excellent |
| Reproducibility | Better (exact image) | Good (pinned deps) |

**Decision**: Native. The marginal reproducibility benefit of Docker does not justify the overhead for a single Mac mini deployment.

---

## 13. Testing and QA Strategy

### Test Layers

| Layer | Tool | What It Tests |
|-------|------|---------------|
| Unit | pytest | Individual functions, parsers, classifiers, scoring logic |
| Integration | pytest + httpx | API endpoints, database operations, agent pipelines |
| Parser | pytest | Each source parser against saved fixtures |
| Event Mapping | pytest | Rule engine accuracy, link correctness |
| False Positive/Negative | pytest + golden set | Known events → expected holdings mapping |
| Reconciliation | pytest | Duplicate handling, conflict detection |
| Regression | pytest | Previous bugs don't recur |
| Security | bandit + safety | No secrets in code, dependency vulnerabilities |
| Audit | pytest | Every mutation creates audit entry |
| Smoke | bash scripts | Mac mini installation works end-to-end |

### Test Structure

```
tests/
├── unit/
│   ├── test_ledger.py
│   ├── test_classifier.py
│   ├── test_impact_engine.py
│   ├── test_parsers/
│   │   ├── test_rss_parser.py
│   │   ├── test_sec_parser.py
│   │   └── fixtures/           # Saved source responses
│   └── test_risk_calculator.py
├── integration/
│   ├── test_intake_flow.py
│   ├── test_collection_flow.py
│   ├── test_analysis_flow.py
│   └── test_api_endpoints.py
├── golden/
│   ├── test_event_mapping_golden.py   # Known-good event→holding mappings
│   └── golden_data/
└── smoke/
    ├── test_installation.sh
    ├── test_startup.sh
    └── test_health.sh
```

---

## 14. Observability / Operations Plan

### Logging

- **Format**: JSON lines (structured, parseable)
- **Location**: `~/kleitos-data/logs/`
- **Rotation**: Daily, keep 30 days
- **Levels**: DEBUG (dev), INFO (production), WARNING, ERROR

### Key Metrics (stored in `system_health` table)

| Metric | Frequency | Alert Threshold |
|--------|-----------|-----------------|
| API response time (p95) | Per request | > 5 seconds |
| Collection cycle duration | Per run | > 10 minutes |
| Events collected per cycle | Per run | 0 (no events = possible failure) |
| Source failures | Per run | > 3 sources failed |
| Database size | Daily | > 5 GB |
| Holdings without classification | Per run | > 0 (should be zero) |
| Coverage gaps | Per run | Any critical gap |
| Undelivered alerts | Hourly | > 0 for > 1 hour |

### Dead-Letter / Failed Jobs

- Failed agent runs are logged in `agent_runs` table with status=`failed` and error message
- Failed events (unparseable) are stored in `events` with `materiality='parse_error'`
- A daily cleanup job reports on all failures
- Manual retry: `POST /api/v1/agents/{agent_id}/retry`

### Source Outage Handling

- If a source fails 3 consecutive times, it is marked `degraded`
- After 10 failures, it is auto-disabled with an alert to the client
- Source health is checked before each fetch cycle
- Manual re-enable: edit `config/sources.yaml` or `POST /api/v1/sources/{id}/enable`

### Manual Override Workflow

- Operator can manually classify a holding: `POST /api/v1/securities/{id}/classify`
- Operator can manually create an event: `POST /api/v1/events/manual`
- Operator can acknowledge/dismiss alerts: `POST /api/v1/alerts/{id}/acknowledge`
- All manual actions are logged in audit trail

---

## 15. Reporting and UI Plan

### Minimum Viable Views

| View | Content | Access |
|------|---------|--------|
| **Holdings** | Sortable table of all holdings with ticker, name, sector, geography, weight, last event | Dashboard |
| **Exposures** | Sector pie, geography bar, currency breakdown, theme tags, top 10 positions | Dashboard |
| **Digest** | Latest daily digest, historical digest list, per-holding summaries | Dashboard + Chat |
| **Alerts** | Active alerts with severity, related holdings, acknowledge button | Dashboard + Chat |
| **Audit Trail** | Searchable log of all changes, filterable by entity/agent/date | Dashboard |
| **System Health** | Agent run history, source health, next scheduled runs, error count | Dashboard |
| **Analysis Notes** | Per-event analysis with source traces, per-holding analysis history | Dashboard |

---

## 16. MVP vs V2 vs V3 Scope

### MVP (Phase 0-5, Weeks 1-12)

| Feature | Included |
|---------|----------|
| Portfolio ledger (CSV/JSON import) | Yes |
| Security classification (LLM-assisted) | Yes |
| Source registry with 5-10 sources | Yes |
| Event collection and deduplication | Yes |
| Rule-based impact mapping | Yes |
| LLM-assisted impact scoring | Yes |
| Coverage QA | Yes |
| Portfolio analysis notes | Yes |
| Daily digest | Yes |
| Risk alerts (concentration, material events) | Yes |
| OpenClaw Commander with chat interface | Yes |
| Web dashboard (all core views) | Yes |
| macOS launchd deployment | Yes |
| Backup/restore | Yes |
| Audit trail | Yes |

### V2 (Month 3-4)

| Feature | Notes |
|---------|-------|
| Excel import for portfolio data | Common client format |
| 20+ source parsers | Broader coverage |
| Email digest delivery | In addition to chat |
| Multi-language news | International portfolios |
| Earnings calendar integration | Automated calendar tracking |
| Peer group analysis | Structured competitor comparison |
| Historical trend analysis | How exposures changed over time |
| Dashboard charts (charts.js or similar) | Richer visualizations |
| Source parser auto-healing | Detect and adapt to structure changes |
| API key rotation tooling | Security maintenance |

### V3 (Month 5+)

| Feature | Notes |
|---------|-------|
| Real-time streaming (WebSocket) | Instant event delivery |
| Brokerage API integration | Direct portfolio sync |
| Multi-portfolio support | Multiple clients/strategies |
| Custom alert rules (user-defined) | Flexible thresholds |
| PDF report generation | Formal client reports |
| Mobile companion app | OpenClaw iOS/Android |
| Voice interaction | OpenClaw voice mode |
| Vector search for semantic event matching | Beyond keyword matching |
| Backtesting framework | Historical event replay |

### Explicitly Do NOT Build Early

- Brokerage API integration (complex, security-sensitive, not needed for MVP)
- Real-time streaming (daily cycle is sufficient initially)
- Multi-tenancy (single client)
- Machine learning model training (use pre-trained LLMs)
- Custom dashboarding framework (use simple HTML + htmx)
- Mobile app (OpenClaw handles mobile via messaging)

---

## 17. Implementation Risks and Failure Modes

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | Bad classification (wrong sector/geography) | Medium | High | LLM + human review for low-confidence; golden test set |
| R2 | Broken parsers (source HTML changes) | High | Medium | Parser drift detection; fixture-based tests; alerts on parse failures |
| R3 | Silent coverage failure (events missed) | Medium | High | Coverage QA agent; gap alerting; multiple sources per topic |
| R4 | Poor relevance scoring (false positives/negatives) | Medium | Medium | Thresholds + human feedback loop; golden test set |
| R5 | Noisy alerts (too many low-value alerts) | High | Medium | Materiality thresholds; severity-based filtering; tunable parameters |
| R6 | Overconfident LLM analysis | Medium | High | Confidence scoring; source traces mandatory; "analysis pending" fallback |
| R7 | Incorrect portfolio reconciliation | Low | Critical | Conflict detection; never auto-overwrite; audit trail |
| R8 | Systemic event misclassification | Low | High | Market-wide keywords list; multi-factor scope detection |
| R9 | Bad source control (unauthorized sources) | Low | High | Allowlist enforcement in code; domain validation; audit logging |
| R10 | macOS deployment fragility | Medium | Medium | launchd with KeepAlive; health checks; restart scripts |
| R11 | API key exposure | Low | Critical | File permissions (600); no secrets in code; .env template |
| R12 | SQLite corruption | Very Low | Critical | WAL mode; daily backups; integrity checks in health check |
| R13 | OpenClaw version breaking changes | Medium | Medium | Pin version; test updates before deploying |
| R14 | LLM API outage | Low | Medium | Graceful degradation; cached results; retry with backoff |
| R15 | Disk space exhaustion | Low | Medium | Log rotation; old backup cleanup; monitoring |

---

## 18. Final Recommended Stack and Repo Structure

### Technology Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| **Language** | Python 3.11+ | Best financial ecosystem, easy macOS install, great LLM libraries |
| **Backend Framework** | FastAPI | Async, fast, modern, great docs, built-in OpenAPI |
| **Database** | SQLite (WAL mode) | Zero-config, single file, perfect for single-user, no server needed |
| **ORM** | SQLAlchemy 2.0 | Mature, type-safe, great SQLite support |
| **Job Scheduler** | APScheduler | Built-in Python, no external dependencies, persistent jobs |
| **HTTP Client** | httpx | Async, modern, timeout/retry support |
| **LLM SDK** | anthropic | Official Anthropic Python SDK |
| **Template Engine** | Jinja2 | For digest/report generation |
| **Frontend** | HTML + htmx + Pico CSS | No build step, simple deployment, progressive enhancement |
| **Process Manager** | macOS launchd | Native, reliable, survives reboots |
| **OpenClaw** | Latest stable | Multi-agent command center |
| **Testing** | pytest + httpx | Standard Python testing |
| **Linting** | ruff | Fast, comprehensive |

### Repository Structure

```
kleitos/
├── ARCHITECTURE.md              # This document
├── README.md                    # Quick start guide
├── pyproject.toml               # Python project config
├── requirements.txt             # Pinned dependencies
├── .env.template                # Environment variable template
├── .gitignore
│
├── install.sh                   # One-command bootstrap
├── setup.sh                     # Configuration wizard
├── start.sh                     # Start all services
├── stop.sh                      # Stop all services
├── status.sh                    # Health/status check
├── update.sh                    # Update system
├── healthcheck.sh               # Detailed health check
│
├── config/
│   ├── settings.yaml            # Main configuration
│   ├── sources.yaml             # Source registry
│   ├── risk_thresholds.yaml     # Risk alert thresholds
│   └── launchd/
│       ├── com.kleitos.core.plist
│       └── com.kleitos.openclaw.plist
│
├── src/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app entry point
│   ├── config.py                # Configuration loader
│   ├── database/
│   │   ├── __init__.py
│   │   ├── connection.py        # DB connection manager
│   │   ├── models.py            # SQLAlchemy models
│   │   └── migrations.py        # Schema migrations
│   ├── ledger/
│   │   ├── __init__.py
│   │   ├── portfolio.py         # Portfolio CRUD
│   │   ├── reconciliation.py    # Reconciliation logic
│   │   └── audit.py             # Audit trail
│   ├── security_master/
│   │   ├── __init__.py
│   │   ├── classifier.py        # Classification engine
│   │   └── exposure.py          # Exposure calculations
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── registry.py          # Source registry
│   │   ├── fetcher.py           # HTTP fetcher
│   │   └── parsers/
│   │       ├── __init__.py
│   │       ├── base.py
│   │       ├── rss_generic.py
│   │       └── newsapi.py
│   ├── events/
│   │   ├── __init__.py
│   │   ├── store.py             # Event storage
│   │   ├── dedup.py             # Deduplication
│   │   └── linker.py            # Event-holding linking
│   ├── impact/
│   │   ├── __init__.py
│   │   ├── engine.py            # Impact mapping engine
│   │   ├── rules.py             # Rule-based matching
│   │   └── scoring.py           # LLM-based scoring
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py              # Base agent class
│   │   ├── intake.py
│   │   ├── classification.py
│   │   ├── collection.py
│   │   ├── coverage_qa.py
│   │   ├── analysis.py
│   │   └── risk.py
│   ├── reporting/
│   │   ├── __init__.py
│   │   ├── digests.py
│   │   ├── alerts.py
│   │   └── summaries.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── middleware.py
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── portfolio.py
│   │       ├── events.py
│   │       ├── analysis.py
│   │       ├── alerts.py
│   │       ├── digests.py
│   │       ├── sources.py
│   │       ├── agents.py
│   │       ├── audit.py
│   │       └── health.py
│   └── scheduler/
│       ├── __init__.py
│       └── jobs.py
│
├── dashboard/
│   ├── index.html
│   ├── css/
│   │   └── styles.css
│   ├── js/
│   │   └── app.js
│   └── templates/
│       ├── base.html
│       ├── holdings.html
│       ├── exposures.html
│       ├── digest.html
│       ├── alerts.html
│       ├── audit.html
│       └── health.html
│
├── openclaw/
│   ├── setup-openclaw.sh
│   ├── openclaw-config.json
│   └── workspaces/
│       ├── commander/
│       │   ├── AGENTS.md
│       │   ├── SOUL.md
│       │   ├── USER.md
│       │   └── skills/
│       ├── intake/
│       ├── classification/
│       ├── collection/
│       ├── coverage/
│       ├── analysis/
│       └── risk/
│
├── tests/
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   ├── golden/
│   └── smoke/
│
├── scripts/
│   ├── backup.sh
│   ├── restore.sh
│   └── reset-db.sh
│
└── docs/
    ├── INSTALL.md
    ├── OPERATIONS.md
    └── TROUBLESHOOTING.md
```

---

## 19. Step-by-Step Build Sequence

### Sprint 1 (Days 1-3): Foundation

1. Create repo structure (all directories)
2. Write `pyproject.toml` and `requirements.txt`
3. Implement `src/config.py` (YAML + env loader)
4. Implement `src/database/connection.py` (SQLite setup with WAL)
5. Implement `src/database/models.py` (all SQLAlchemy models)
6. Implement `src/database/migrations.py` (table creation)
7. Create `src/main.py` (FastAPI app with health endpoint)
8. Write `install.sh` (brew + python + venv + deps)
9. Write `setup.sh` (create dirs, init db, create .env)
10. Write `.env.template`
11. Write `config/settings.yaml`
12. First test: `pytest tests/unit/test_database.py`

### Sprint 2 (Days 4-6): Portfolio Ledger + Intake

1. Implement `src/ledger/portfolio.py` (CRUD)
2. Implement `src/ledger/audit.py` (audit trail)
3. Implement `src/ledger/reconciliation.py`
4. Implement `src/agents/base.py` (base agent class)
5. Implement `src/agents/intake.py`
6. Implement `src/api/routes/portfolio.py`
7. Create CSV parser for portfolio import
8. Tests: `test_ledger.py`, `test_intake.py`, `test_portfolio_api.py`

### Sprint 3 (Days 7-9): Classification + Dashboard Shell

1. Implement `src/security_master/classifier.py`
2. Implement `src/security_master/exposure.py`
3. Implement `src/agents/classification.py`
4. Implement `src/api/routes/portfolio.py` (exposure endpoints)
5. Create `dashboard/` HTML shell with htmx
6. Create holdings view
7. Create exposure view
8. Tests: `test_classifier.py`, `test_exposure.py`

### Sprint 4 (Days 10-14): Sources + Collection + OpenClaw

1. Write `config/sources.yaml` (initial 5 sources)
2. Implement `src/sources/registry.py`
3. Implement `src/sources/fetcher.py`
4. Implement first 3 parsers (RSS, NewsAPI, basic)
5. Implement `src/events/store.py`
6. Implement `src/events/dedup.py`
7. Implement `src/events/linker.py`
8. Implement `src/agents/collection.py`
9. Implement `src/scheduler/jobs.py`
10. Set up OpenClaw with Commander workspace
11. Create first OpenClaw skills
12. Write `start.sh`, `stop.sh`, `status.sh`
13. Tests: `test_parsers.py`, `test_collection.py`

---

## 20. Definition of Done

The platform is production-ready when ALL of the following are true:

### Functional Criteria
- [ ] Portfolio data can be imported via CSV/JSON
- [ ] All holdings are classified (sector, geography, currency, themes)
- [ ] Exposure views correctly aggregate portfolio data
- [ ] At least 5 news sources are actively collecting
- [ ] Events are deduplicated and linked to holdings
- [ ] Impact mapping produces scored, explained links
- [ ] Coverage QA detects and reports gaps
- [ ] Analysis notes are generated for material events
- [ ] Daily digest is automatically generated and delivered
- [ ] Risk alerts fire for concentration/calendar/material events
- [ ] All 6 agents execute without errors
- [ ] Commander Agent handles basic portfolio queries via chat

### Operational Criteria
- [ ] `install.sh` completes on fresh Mac mini in < 15 minutes
- [ ] System starts on boot via launchd
- [ ] System recovers from crash (KeepAlive)
- [ ] `healthcheck.sh` reports all-green status
- [ ] Logs are rotated and manageable
- [ ] Daily backups run automatically
- [ ] `update.sh` works without data loss
- [ ] Dashboard is accessible at http://localhost:7777

### Quality Criteria
- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] Golden test set (known events → expected mappings) passes
- [ ] No secrets in codebase
- [ ] Audit trail captures all mutations
- [ ] No agent exceeds its declared permissions

---

## 21. Client Mac Mini Installation Instructions

### Prerequisites

- Mac mini (Apple Silicon recommended, Intel supported)
- macOS 14 (Sonoma) or later
- Internet connection
- Anthropic API key (get from https://console.anthropic.com)
- One messaging app for OpenClaw (Telegram recommended for easiest setup)

### One-Command Install

```bash
# Clone the repository and run the installer
git clone https://github.com/YOUR_ORG/kleitos.git ~/kleitos
cd ~/kleitos
chmod +x install.sh && ./install.sh
```

### What `install.sh` Does

1. Installs Homebrew (if not present)
2. Installs Python 3.11+ via Homebrew
3. Installs Node.js 18+ via Homebrew (for OpenClaw)
4. Creates Python virtual environment at `~/kleitos/.venv`
5. Installs all Python dependencies
6. Installs OpenClaw globally (`npm install -g openclaw`)
7. Creates data directory at `~/kleitos-data/`
8. Initializes SQLite database with schema
9. Copies `.env.template` to `~/.kleitos.env`
10. Installs launchd service definitions

### Configuration (after install)

```bash
cd ~/kleitos
./setup.sh
```

`setup.sh` will prompt you for:
1. Anthropic API key (stored in `~/.kleitos.env`)
2. Any source API keys (optional, for premium sources)
3. OpenClaw channel setup (runs `openclaw onboard`)

### First Run

```bash
# Start all services
./start.sh

# Check everything is healthy
./status.sh

# Open the dashboard
open http://localhost:7777
```

### How to Use

1. **Dashboard**: Open `http://localhost:7777` in a browser (or `http://[mac-mini-ip]:7777` from another device on the same network)
2. **Chat**: Message the OpenClaw bot on your configured channel (Telegram, Signal, etc.)
3. **Upload Portfolio**: Use the dashboard upload button or send a CSV to the chat bot

### Common Operations

| Operation | Command |
|-----------|---------|
| Start everything | `cd ~/kleitos && ./start.sh` |
| Stop everything | `cd ~/kleitos && ./stop.sh` |
| Check health | `cd ~/kleitos && ./status.sh` |
| Detailed health | `cd ~/kleitos && ./healthcheck.sh` |
| Update system | `cd ~/kleitos && ./update.sh` |
| Manual backup | `cd ~/kleitos && ./scripts/backup.sh` |
| Restore backup | `cd ~/kleitos && ./scripts/restore.sh [file]` |
| View logs | `tail -f ~/kleitos-data/logs/kleitos.log` |
| View error logs | `tail -f ~/kleitos-data/logs/kleitos-error.log` |

### Troubleshooting

| Problem | Fix |
|---------|-----|
| Dashboard not loading | `./status.sh` — check if API is running. If not: `./start.sh` |
| No new events | Check source health in dashboard. Check internet. `./healthcheck.sh` |
| OpenClaw not responding | Check `status.sh`. Restart: `./stop.sh && ./start.sh` |
| Database locked | Stop services, check for rogue processes: `lsof ~/kleitos-data/db/kleitos.db` |
| Disk full | Check `du -sh ~/kleitos-data/`. Run `./scripts/cleanup-old-backups.sh` |

### How to Uninstall

```bash
cd ~/kleitos
./stop.sh
launchctl unload ~/Library/LaunchAgents/com.kleitos.core.plist
launchctl unload ~/Library/LaunchAgents/com.kleitos.openclaw.plist
rm ~/Library/LaunchAgents/com.kleitos.*.plist
# Optionally remove data:
# rm -rf ~/kleitos-data
# rm ~/.kleitos.env
```

---

## 22. First 14 Days Execution Plan

### Day 1-2: Foundation
- Set up repo structure
- Write all config files
- Implement database schema and connection
- Write install.sh and setup.sh
- Deploy FastAPI skeleton with health endpoint
- Run on Mac mini, verify launchd start

### Day 3-4: Portfolio Ledger
- Implement portfolio CRUD
- Implement audit trail
- Build CSV/JSON intake parser
- Build reconciliation engine
- Create portfolio API endpoints
- Test: upload CSV, verify in DB

### Day 5-6: Classification + Dashboard
- Implement security classifier (rules + LLM)
- Implement exposure calculator
- Build API endpoints for exposures
- Create dashboard HTML shell
- Build holdings view and exposure view
- Test: classify holdings, see exposures in dashboard

### Day 7-8: Source Registry + Collection
- Write sources.yaml with 5 initial sources
- Implement source registry
- Implement HTTP fetcher with rate limiting
- Build 3 parser adapters (RSS, NewsAPI, generic)
- Implement event store with deduplication
- Set up APScheduler
- Test: run collection, events appear in DB

### Day 9-10: Event Linking + Coverage
- Implement event-to-holding linker
- Build impact mapping rule engine
- Implement Coverage QA agent
- Create events view in dashboard
- Test: events linked to correct holdings

### Day 11-12: Analysis + Digest
- Implement LLM-based impact scoring
- Build Analysis Agent
- Build digest generator
- Create digest view in dashboard
- Set up OpenClaw Commander workspace
- Test: daily digest generated

### Day 13-14: Risk + Integration
- Implement Risk Agent
- Build concentration monitoring
- Create alerts view in dashboard
- End-to-end integration test
- Write start/stop/status/healthcheck scripts
- Test full pipeline: CSV → holdings → events → analysis → digest → alert

---

## 23. Questions That Should Be Answered Before Coding Starts

These questions should be resolved but **do not block** the rest of the plan. Reasonable defaults are assumed.

1. **Which messaging channel does the client prefer?** (Default: WebChat for initial testing, Telegram for production)
2. **What is the initial portfolio size?** (Default: < 100 holdings)
3. **What news sources should be included first?** (Default: Reuters RSS, SEC EDGAR, Yahoo Finance, NewsAPI, Financial Times RSS)
4. **What is the preferred digest schedule?** (Default: daily at 7:00 AM local time)
5. **Should the dashboard be accessible from other devices on the LAN?** (Default: yes, bind to 0.0.0.0)
6. **What risk thresholds should be used?** (Default: concentration > 10% per name, > 30% per sector, > 40% per geography)
7. **Does the client have premium data source subscriptions?** (Default: no, use free sources only)
8. **What is the preferred language for analysis?** (Default: English)
9. **Should the system handle multiple portfolios?** (Default: no, single portfolio in MVP)
10. **What is the preferred backup retention?** (Default: 7 daily backups)
11. **Is there a VPN or network restriction on the Mac mini?** (Default: no)
12. **Does the client want email delivery of digests?** (Default: no, chat only in MVP)
13. **What Apple Silicon Mac mini model?** (Default: M2 or later, 16GB+ RAM)
14. **Is the client comfortable with a terminal for initial setup?** (Default: yes, with guided script)
15. **Should analysis notes be conservative or exploratory?** (Default: conservative — state facts, flag uncertainties)

---

*End of Architecture Document*
*Next: Implementation scaffolding and code files*
