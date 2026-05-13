# Axion

Portfolio intelligence by 4Labs. A local desktop application that monitors news and events affecting your holdings, tracks exposures, and surfaces risk concentrations.

## Run it

This is a **single-user, local** application. No Docker, no cloud, no admin rights.

**macOS / Linux:**
```bash
./scripts/run_local.sh
```

**Windows (PowerShell):**
```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\run_local.ps1
```

Or **double-click `Axion.bat`** on Windows.

Dashboard opens at <http://127.0.0.1:7777/dashboard/>.

Full instructions: **[README_LOCAL.md](README_LOCAL.md)** and **[docs/CUSTOMER_QUICKSTART.md](docs/CUSTOMER_QUICKSTART.md)**.

## Requirements

- Python 3.11+
- ~300 MB disk for venv + dependencies
- Internet access (first run + ongoing news collection)

## What it does

- **Monitors** approved news sources (RSS, APIs) on a 30-minute cycle.
- **Matches** events to holdings by ticker, sector, geography, currency, and theme.
- **Scores** impact using deterministic rules (plus optional LLM-enhanced analysis if you add an Anthropic / OpenAI / Google API key).
- **Alerts** on material developments with severity classification.
- **Reports** daily digests via dashboard, Telegram, or email.
- **Tracks** portfolio exposures and concentration risks.

## What it is NOT

- Not a real-time market data terminal (30-min cycle, news-driven).
- Not a broker (no order routing).
- Not a multi-user cloud service (loopback-only, one machine).

See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for the honest list.

## Architecture

Six specialised agents coordinate through a shared SQLite database:

| Agent | Role |
|-------|------|
| **Intake** | Ingests portfolio CSVs, enriches holdings |
| **Classification** | Tags events across 7 dimensions |
| **Collection** | Fetches from approved sources on schedule |
| **Coverage QA** | Ensures every holding has recent coverage |
| **Analysis** | Produces narratives and thesis tracking |
| **Risk** | Monitors concentration and generates alerts |

Full design: [ARCHITECTURE.md](ARCHITECTURE.md).

## Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| API | FastAPI + Uvicorn |
| Database | SQLite (WAL mode) |
| Scheduler | APScheduler |
| LLM (optional) | Anthropic / OpenAI / Google — system runs without it |
| Notifications | Telegram bot, email (both optional) |
| Dashboard | Vanilla JS + custom CSS |

## Project layout

```
.
├── src/                    # Application code (FastAPI, agents, DB models, …)
│   ├── api/routes/         # REST endpoints
│   ├── agents/             # 6 domain agents
│   ├── database/           # Connection, models, migrations
│   ├── sources/            # Source registry, fetcher, parsers
│   └── …
├── config/                 # YAML configs (sources, settings, risk thresholds)
├── dashboard/              # Static HTML/CSS/JS
├── scripts/
│   ├── run_local.sh        # macOS/Linux launcher
│   ├── run_local.ps1       # Windows launcher
│   ├── smoke_local.py      # 16-check end-to-end smoke test
│   └── …
├── Axion.bat               # Windows double-click launcher
├── Axion.app/              # macOS .app bundle (alternative to run_local.sh)
├── sample_portfolio.csv    # Demo data
├── README_LOCAL.md         # Local install guide
├── KNOWN_LIMITATIONS.md    # Honest list of what doesn't work
└── docs/
    ├── CUSTOMER_QUICKSTART.md
    ├── RELEASE_CHECKLIST.md
    ├── DEMO_RESET.md
    └── …
```

## Verify your install

```bash
.venv/bin/python scripts/smoke_local.py   # macOS / Linux
.venv\Scripts\python.exe scripts\smoke_local.py   # Windows
```

Runs 16 end-to-end checks (migrations, default portfolio, CSV import, dashboard, websocket, exports, settings) against a throwaway temp DB. Your real data is not touched.

## Documentation

- **Customer quick start** — [docs/CUSTOMER_QUICKSTART.md](docs/CUSTOMER_QUICKSTART.md)
- **Local install** — [README_LOCAL.md](README_LOCAL.md)
- **Reset / recovery** — [docs/DEMO_RESET.md](docs/DEMO_RESET.md)
- **Release readiness** — [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)
- **Operator runbook** — [OPERATOR_CHECKLIST.md](OPERATOR_CHECKLIST.md)
- **Architecture** — [ARCHITECTURE.md](ARCHITECTURE.md)
- **Honest limitations** — [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md)

## License

© 4Labs. All rights reserved.
