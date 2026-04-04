# Axion — Installation Guide

## Quick Start

### macOS
Double-click **Axion.app**. First launch may require right-click → Open (Gatekeeper approval). Setup takes 2-5 minutes and the dashboard opens automatically.

### Windows
Double-click **Axion.bat**. Requires Python 3.11+ ([python.org/downloads](https://www.python.org/downloads/) — check "Add to PATH"). First launch takes 2-5 minutes.

### Dashboard
Open `http://localhost:7777` in any browser.

## Requirements

- **macOS 12+** (Apple Silicon) or **Windows 10/11** with Python 3.11+
- 8 GB RAM minimum
- No Docker, databases, or servers to configure

## What Happens on First Launch

1. Python virtual environment is created
2. All dependencies are installed automatically
3. The database is initialized
4. News collection starts on a 30-minute cycle
5. The dashboard opens in your browser or native window

## Daily Use

### Axion runs 24/7 in the background

| Task | Frequency |
|------|-----------|
| News collection | Every 30 min |
| Event analysis | Every 30 min |
| Risk assessment | Every hour |
| Daily digest | 7:00 AM |
| Database backup | 2:00 AM |

### After a restart
Axion starts automatically if auto-start was configured during install.

### Add an AI Provider (Optional)
In the dashboard, go to **Settings → AI Provider**, select Anthropic/OpenAI/Google, and enter your API key. All core features work without AI.

## Multi-Portfolio

Use the portfolio selector in the top navigation bar to create and switch between portfolios. Each portfolio has its own holdings, alerts, and digests.

## Uninstall

### macOS
```bash
./scripts/uninstall-mac.sh
```

### Windows
```powershell
powershell -ExecutionPolicy Bypass -File scripts\uninstall-windows.ps1
```

Your data is preserved at `~/axion-data/` (or `~/kleitos-data/`) — delete manually to fully clean up.

## Advanced Installation (Operators)

### macOS Terminal Install
```bash
chmod +x scripts/install-mac.sh && ./scripts/install-mac.sh
```
Installs to /Applications, sets up auto-start on boot.

### Windows Full Install
```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
```
Creates desktop/Start Menu shortcuts, auto-start on login, Add/Remove Programs entry.
