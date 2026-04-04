# Axion by 4Labs — Client Delivery Guide

This guide describes how to prepare and deliver Axion to a client or operator.

## Automated Package Generation

Run the packaging script to generate a clean delivery folder:

```bash
# Windows
.venv\Scripts\python scripts\prepare-delivery.py --output C:\Delivery

# macOS/Linux
python3 scripts/prepare-delivery.py --output ~/Delivery

# Options:
#   --no-docs     Skip optional docs/ folder
#   --no-macos    Skip macOS-specific files (Axion.app, shell scripts)
#   --no-docker   Skip Docker files
```

This creates an `Axion/` folder at the specified location with only the files needed
for client delivery. The script automatically excludes internal dev artifacts, legacy
files, and test code. It also verifies the package contents.

## What The Client Receives

A folder containing the Axion application, ready to run.

### Required Files and Folders

| Item | Purpose |
|------|---------|
| `Axion.bat` | Windows double-click launcher (auto-setup) |
| `src/` | Application code |
| `config/` | Settings, sources, prompts, risk thresholds |
| `dashboard/` | Web UI (HTML/JS/CSS) |
| `scripts/` | Tray app, menubar app, install/stop helpers |
| `requirements.txt` | Python dependencies |
| `.env.template` | Configuration template with guidance |
| `Dockerfile` | Docker deployment option |
| `docker-compose.yml` | Docker Compose configuration |

### Recommended Documentation to Include

| File | Purpose |
|------|---------|
| `README.md` | Product overview and quick start |
| `INSTALL.md` | Detailed install instructions |
| `OPERATOR_CHECKLIST.md` | Step-by-step first-week guide |
| `KNOWN_LIMITATIONS.md` | Honest scope and constraints |
| `RELEASE_NOTES_V1.md` | What's included in this release |
| `sample_portfolio.csv` | Example portfolio for first-run testing |
| `START_HERE.txt` | Quick-start instructions for the client |

### Optional (Include If Relevant)

| Item | When to Include |
|------|-----------------|
| `openclaw/` | Only if OpenClaw multi-agent is part of the delivery |
| `Axion.app/` | Only for macOS delivery |
| `assets/` | Only if building custom .exe or .app |

### Files to Exclude from Client Delivery

These are internal development/engineering artifacts:

| Item | Reason |
|------|--------|
| `ARCHITECTURE.md` | 86KB internal design spec — not client-facing |
| `RELEASE_HARDENING_LOG.md` | Internal engineering log |
| `RELEASE_BACKLOG.md` | Internal issue tracker |
| `RELEASE_DECISIONS.md` | Internal decision log |
| `RELEASE_READINESS_CHECKLIST.md` | Internal QA checklist |
| `DELIVERY_GUIDE.md` | This file — internal only |
| `test_api.py` | Test script — internal |
| `test_full_pipeline.py` | Test script — internal |
| `tests/` | Test suite — internal |
| `install.sh`, `setup.sh`, etc. | Developer ops scripts — not for client delivery |
| `healthcheck.sh`, `start.sh`, etc. | Server control — internal only |
| `pyproject.toml` | Dev tooling config |
| `.venv/` | Created automatically by launcher |
| `kleitos-data/` | Created automatically at runtime |
| `__pycache__/` | Python cache — exclude |
| `.git/` | Version control — exclude |

## Platform-Specific Delivery

### Windows Delivery

**Recommended folder name:** `Axion`

**Delivery layout:**
```
Axion/
├── Axion.bat              ← Double-click to start
├── README.md
├── INSTALL.md
├── OPERATOR_CHECKLIST.md
├── KNOWN_LIMITATIONS.md
├── RELEASE_NOTES_V1.md
├── .env.template
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── src/
├── config/
├── dashboard/
└── scripts/
```

**Instructions for operator:**
1. Place the `Axion` folder where desired (e.g., `C:\Users\<name>\Axion`)
2. Double-click `Axion.bat`
3. On first launch: auto-installs Python venv + dependencies (1-2 min)
4. Dashboard opens in browser at `http://localhost:7777`
5. Desktop shortcut and Start Menu entry are created automatically
6. Auto-starts on login via Windows Task Scheduler

### macOS Delivery

**Recommended folder name:** `axion`

**Delivery layout:**
```
axion/
├── Axion.app/             ← Double-click launcher (macOS app bundle)
├── README.md
├── INSTALL.md
├── OPERATOR_CHECKLIST.md
├── KNOWN_LIMITATIONS.md
├── RELEASE_NOTES_V1.md
├── .env.template
├── requirements.txt
├── install.sh
├── start.sh
├── stop.sh
├── src/
├── config/
│   └── launchd/
│       ├── com.axion.core.plist
│       └── com.axion.openclaw.plist
├── dashboard/
└── scripts/
    ├── install-mac.sh
    └── axion-menubar.py
```

**Instructions for operator:**
1. Place the `axion` folder in home directory: `~/axion`
2. Run `chmod +x scripts/install-mac.sh && ./scripts/install-mac.sh`
3. Dashboard opens at `http://localhost:7777`
4. Auto-starts on login via launchd

### Docker Delivery

**For containerized deployment:**
```bash
docker compose up --build -d
# Dashboard at http://localhost:7777
```

## Pre-Delivery Testing Checklist

Before sending to a client, verify:

- [ ] `Axion.bat` (or install script) runs successfully on a clean machine
- [ ] Dashboard loads at `http://localhost:7777/dashboard`
- [ ] Health endpoint returns `{"status":"ok"}`
- [ ] Portfolio upload works (test with sample CSV)
- [ ] Events collect after 30 minutes (or manual trigger)
- [ ] `.env.template` is present and has clear guidance
- [ ] No `.env` file with real secrets is included
- [ ] No `~/.axion.env` from testing is included
- [ ] No `kleitos-data/` directory with test data is included
- [ ] Documentation is current and accurate

## Sample Portfolio CSV

Include a sample CSV file for the client to test with. Format:

```csv
ticker,quantity,price,currency,isin
AAPL,100,175.50,USD,US0378331005
MSFT,50,420.00,USD,US5949181045
NVDA,25,890.00,USD,US67066G1040
```

Save this as `sample_portfolio.csv` in the delivery folder.

## Post-Delivery Support Notes

After delivery:
1. Client should follow `OPERATOR_CHECKLIST.md` for first-week verification
2. API key can be configured from the dashboard Settings tab
3. Data is stored in `~/kleitos-data/` (Windows: `%USERPROFILE%\kleitos-data\`)
4. Logs are in `~/kleitos-data/logs/`
5. Backups run automatically at 02:00, stored in `~/kleitos-data/backups/`
6. For troubleshooting, refer to `docs/TROUBLESHOOTING.md` if included
