# Axion — Installation Guide

## Requirements

- **macOS 12+** (Monterey or later)
- **Mac Mini** with 8 GB RAM minimum
- That's it. No Docker. No databases. No servers to configure.

## Install (One Command)

Open Terminal, navigate to the Axion folder, and run:

```bash
chmod +x scripts/install-mac.sh && ./scripts/install-mac.sh
```

The installer handles everything:
- Installs Python 3.12 (via Homebrew, if needed)
- Creates a virtual environment with all dependencies
- Installs **Axion.app** to `/Applications`
- Sets up auto-start on boot
- Starts Axion and opens the dashboard

**After install, you never need Terminal again.**

## Daily Use

### Open the Dashboard
- **Spotlight** (Cmd + Space) → type **Axion** → press Enter
- Or open `http://localhost:7777` in any browser

### What Runs Automatically
Axion runs 24/7 in the background:

| Task | Frequency |
|------|-----------|
| News collection | Every 30 min |
| Event analysis | Every 30 min |
| Security classification | Every 6 hours |
| Coverage QA | Every 4 hours |
| Risk assessment | Every 1 hour |
| Daily digest | 7:00 AM |
| Database backup | 2:00 AM |

### After a Restart or Power Outage
Axion starts automatically — no action needed.

### Add an Anthropic API Key (Optional)
For AI-powered analysis instead of rule-based:
1. Open the `.env` file in the Axion folder
2. Uncomment and fill in: `ANTHROPIC_API_KEY=sk-ant-your-key`
3. Restart: open Terminal, run `launchctl kickstart -k gui/$(id -u)/com.axion.app`

Without the key, everything works using built-in rule-based analysis.

## Uninstall

```bash
chmod +x scripts/uninstall-mac.sh && ./scripts/uninstall-mac.sh
```

Your data is preserved at `~/kleitos-data/` — delete it manually to fully clean up.
