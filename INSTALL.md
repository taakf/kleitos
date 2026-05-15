# Axion — Installation Guide

## Quick Start

Axion is a local desktop application. Pick the path for your OS.

### macOS / Linux
```bash
./scripts/run_local.sh
```

Or, on macOS, **double-click `Axion.app`** for a Finder-launched experience.

### Windows
```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\run_local.ps1
```

Or **double-click `Axion.bat`**.

### Dashboard
The launcher opens it automatically. If not, visit <http://127.0.0.1:7777/dashboard/>.

## Requirements

- **Python 3.11+** (3.12 recommended)
- macOS 12+ (Apple Silicon) or Windows 10/11 or modern Linux
- ~300 MB disk for the venv + dependencies
- Internet access on first run (pip) and for ongoing news collection
- 8 GB RAM recommended

No Docker, databases, or services to configure. No admin rights required.

## What Happens on First Launch

1. The launcher verifies Python 3.11+.
2. A `.venv/` is created in the project folder.
3. All dependencies in `requirements.txt` are installed.
4. The data directory is created at `~/axion-data/` (or `~/kleitos-data/` if it already exists).
5. The SQLite database is migrated to the current schema.
6. FastAPI/uvicorn starts on `127.0.0.1:7777` (loopback only).
7. The dashboard opens in your default browser.

First-launch setup takes 1–2 minutes. Subsequent launches start in seconds.

## Daily Use

| Task | Frequency |
|------|-----------|
| News collection | Every 30 min |
| Event analysis | Every 30 min |
| Risk assessment | Every hour |
| Daily digest | 07:00 |
| Database backup | 02:00 |

The collector runs in-process. As long as the launcher window is open and `uvicorn` is running, the scheduler ticks.

To run Axion continuously across reboots, see the **legacy operator install** at the bottom of this file. The default local-launcher path does not auto-start on login — that's a deliberate single-user / single-machine design.

### Add an AI Provider (Optional)

In the dashboard, go to **Settings → AI Provider**, select Anthropic / OpenAI / Google, paste your key, and click **Save**. Restart with the launcher. All core features work without AI.

Keys are saved to `~/.axion.env` with `600` permissions.

## Multi-Portfolio

Use the portfolio selector in the top navigation bar to create and switch between portfolios. Each portfolio has its own holdings, alerts, and digests. The default portfolio is named "Main Portfolio" (id `default`).

## Uninstall

The local-launcher install only writes to two places:

- `<project>/.venv/` — the Python virtual environment (project-local).
- `~/axion-data/` — your database, logs, backups, exports.
- `~/.axion.env` — your saved API keys and provider settings.

To uninstall completely, delete those three. The project folder itself can be removed afterwards.

```bash
# macOS / Linux
rm -rf .venv ~/axion-data ~/.axion.env
```

```powershell
# Windows
Remove-Item -Recurse -Force .venv
Remove-Item -Recurse -Force "$env:USERPROFILE\axion-data"
Remove-Item -Force "$env:USERPROFILE\.axion.env"
```

If you used the legacy `Axion.app` install, run `./scripts/uninstall-mac.sh` to also remove `/Applications/Axion.app` and any launchd auto-start entries.

## Legacy Operator Install (advanced / optional)

These predate the local-launcher path. They are heavier (require Homebrew, npm, OpenClaw) and install Axion as a launchd / Task Scheduler service. They still work, but **the supported customer path is `run_local.sh` / `run_local.ps1`**.

### macOS — Terminal install with auto-start
```bash
chmod +x scripts/install-mac.sh && ./scripts/install-mac.sh
```
Installs `Axion.app` to `/Applications`, sets up auto-start on login.

### Windows — Full install
```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
```
Creates desktop / Start Menu shortcuts, auto-start on login, Add/Remove Programs entry.

## Verification

After install, run the smoke test to confirm everything works:

```bash
.venv/bin/python scripts/smoke_local.py   # macOS / Linux
.venv\Scripts\python.exe scripts\smoke_local.py   # Windows
```

It runs 16 end-to-end checks against a throwaway temp DB. `16/16 passed` means the install is healthy.

## See also

- [docs/FINAL_CUSTOMER_HANDOFF.md](docs/FINAL_CUSTOMER_HANDOFF.md) — one-page handoff checklist
- [README_LOCAL.md](README_LOCAL.md) — fastest reference
- [docs/CUSTOMER_QUICKSTART.md](docs/CUSTOMER_QUICKSTART.md) — guided walkthrough
- [docs/DEMO_RESET.md](docs/DEMO_RESET.md) — reset to a clean state
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — common issues
