# Axion

Portfolio intelligence system by 4Labs for hedge fund management. Runs 24/7, monitoring news and events that impact your holdings.

## What It Does

- **Monitors** approved news sources (RSS, APIs) on a 30-minute cycle
- **Matches** events to your portfolio holdings by ticker, sector, geography, currency, and theme
- **Scores** impact using deterministic rules (+ optional LLM-enhanced analysis with Anthropic API key)
- **Alerts** on material developments with severity classification
- **Reports** daily digests via dashboard, Telegram, or email
- **Tracks** portfolio exposures and concentration risks

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

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

## Quick Start

```bash
# Clone and install
git clone <repo-url> ~/axion
cd ~/axion
chmod +x install.sh && ./install.sh

# Configure
cp .env.template ~/.axion.env
# Edit ~/.axion.env – add ANTHROPIC_API_KEY for LLM-enhanced analysis (optional)

# Start
./start.sh

# Check status
./status.sh
```

The API runs at `http://localhost:7777`. The dashboard is at `http://localhost:7777/dashboard`.

## Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| API | FastAPI + Uvicorn |
| Database | SQLite (WAL mode) |
| Scheduler | APScheduler |
| LLM | Anthropic Claude API (optional — system works without it) |
| Notifications | Telegram bot, email (optional) |
| Dashboard | Vanilla JS + custom CSS |
| Service mgmt | macOS launchd / Windows Task Scheduler |

## Project Structure

```
axion/
├── src/                    # Application code
│   ├── agents/             # 6 domain agents
│   ├── api/routes/         # REST endpoints
│   ├── database/           # Connection, models, migrations
│   ├── events/             # Event store, deduplication
│   ├── impact/             # Rule engine + LLM scoring
│   ├── ledger/             # Portfolio management
│   ├── reporting/          # Digests, alerts
│   ├── scheduler/          # APScheduler jobs
│   ├── security_master/    # Security classification, exposures
│   └── sources/            # Registry, fetcher, parsers
├── config/                 # YAML configs, launchd plists
├── dashboard/              # Static HTML/CSS/JS
├── openclaw/               # Multi-agent chat config
├── scripts/                # Backup, restore
├── tests/                  # Unit + integration tests
└── docs/                   # Operational documentation
```

## Documentation

- [Installation Guide](docs/INSTALL.md) – full setup instructions
- [Operations Guide](docs/OPERATIONS.md) – day-to-day management
- [Troubleshooting](docs/TROUBLESHOOTING.md) – common issues and fixes
- [Architecture](ARCHITECTURE.md) – complete system design

## Key Design Principles

1. **Source allowlist only** – no URL is fetched unless its domain is registered in `config/sources.yaml`
2. **Deterministic first** – rule-based matching runs before any LLM call
3. **Audit everything** – every mutation logged with agent_id and timestamps
4. **Native-first** – runs directly on the OS via launchd (macOS) or as a service (Windows); Docker available as alternative
5. **Conservative defaults** – prefer false negatives over false positives in impact scoring

## License

(c) 4Labs. All rights reserved.
