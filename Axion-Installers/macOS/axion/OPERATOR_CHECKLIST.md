# Axion by 4Labs V1.0 — Operator Checklist

Use this checklist when deploying Axion for a new client or on a new machine.

## Pre-Install

- [ ] Python 3.11+ installed (`python --version`)
- [ ] Machine has internet access (RSS feeds require outbound HTTPS)
- [ ] Port 7777 is available

## Install

### Windows
- [ ] Double-click `Axion.bat` — it auto-creates venv and installs dependencies
- [ ] Or manually: `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`

### macOS
- [ ] Run `chmod +x scripts/install-mac.sh && ./scripts/install-mac.sh`
- [ ] Or manually: `python3 -m venv venv && venv/bin/pip install -r requirements.txt`

## Configure

- [ ] Copy `.env.template` to `.env` in the project root
- [ ] (Optional) Set `ANTHROPIC_API_KEY` for AI-enhanced analysis
- [ ] (Optional) Set `KLEITOS_API_KEY` if exposing on LAN (change host to `0.0.0.0` in `config/settings.yaml`)
- [ ] (Optional) Set `KLEITOS_TELEGRAM_TOKEN` + `KLEITOS_TELEGRAM_CHAT_ID` for Telegram notifications
- [ ] Review `config/sources.yaml` — 6 RSS sources enabled by default

## First Start

- [ ] Start: `python -m uvicorn src.main:app --host 127.0.0.1 --port 7777` (or use Axion.bat)
- [ ] Open `http://localhost:7777/dashboard` in browser
- [ ] Verify overview band shows: "Operational" with green dot
- [ ] Verify "Rule-based" or "AI-enhanced" matches your API key configuration
- [ ] Verify source count shows "6 sources" (default)

## Load Portfolio

- [ ] Click "+ Add" or "Upload CSV" in the toolbar
- [ ] Upload CSV with columns: `ticker`, `quantity`, `price`, `currency` (optional: `isin`)
- [ ] Verify holdings appear in the table with names and sectors
- [ ] If sectors show "Unknown" for some tickers, this is expected in rule-based mode — LLM would classify them

## Verify Pipeline

- [ ] Wait 30 minutes for automatic collection, or trigger manually via API: `curl -X POST http://localhost:7777/api/v1/agents/collection/run`
- [ ] Switch to Events tab — verify events appear from RSS sources
- [ ] Switch to Alerts tab — verify coverage/concentration alerts appear after Risk agent runs
- [ ] Switch to Exposures tab — verify sector/geography/currency breakdowns
- [ ] Check Health tab — all agents should show "completed" status

## What Good Looks Like

After 1 hour of operation with a 10-holding portfolio:
- **Events:** 30-80 events collected from RSS feeds
- **Alerts:** 20-50 alerts (mostly medium-severity coverage gaps)
- **Exposures:** Sector/geography/currency breakdown charts populated
- **Health:** All 6 agents showing "completed" with 0 errors
- **Overview band:** Shows "Collected X minutes ago" (not "never")

## What Requires Attention

| Symptom | Likely Cause | Action |
|---------|--------------|--------|
| "Rule-based" in overview | No `ANTHROPIC_API_KEY` set | Set key in `.env` and restart |
| 0 events after 30 min | Network issue or all sources failing | Check Health tab → Source Health |
| "Unknown" sector for tickers | Rule-based classifier doesn't know that ticker | Set API key for LLM classification |
| CNBC source failing | Geo-restriction (HTTP 503) | Normal in some regions — other sources work |

## First Week

- [ ] Day 1: Verify events are collecting, alerts appearing, dashboard rendering
- [ ] Day 2: Check daily digest at 07:00 (Digest tab or API)
- [ ] Day 3: Review alert volume — if too noisy, adjust thresholds in `config/settings.yaml`
- [ ] Day 5: Check database size: `ls -lh ~/kleitos-data/db/kleitos.db`
- [ ] Day 7: Verify backup at `~/kleitos-data/backups/` (auto-runs at 02:00)

## Support / Escalation

- Check `docs/TROUBLESHOOTING.md` for common issues
- Check `docs/OPERATIONS.md` for operational commands
- Check `KNOWN_LIMITATIONS.md` for expected constraints
- Server logs: check terminal output or `~/kleitos-data/logs/`
