# Installation Guide (Developer / Manual Setup)

> **Client install?** See [INSTALL.md](../INSTALL.md) in the project root for the simplified one-command installer.
>
> This guide covers manual setup, developer workflows, and advanced configuration.

## Prerequisites

- Python 3.11 or later (macOS, Windows, or Linux)
- (Optional) Anthropic API key for LLM-enhanced analysis — system works without it using rule-based fallback

## Step 1: Clone the Repository

```bash
cd ~
git clone <repo-url> axion
cd axion
```

## Step 2: Run the Installer

```bash
chmod +x install.sh
./install.sh
```

This will:
- Create a Python virtual environment at `~/axion/venv`
- Install all dependencies from `requirements.txt`
- Create the data directory at `~/kleitos-data/`
- Initialise the SQLite database with all required tables
- Set up the log directory

## Step 3: Configure Environment

```bash
cp .env.template ~/.kleitos.env
chmod 600 ~/.kleitos.env
```

Edit `~/.kleitos.env`:

```
# Optional — enables LLM-enhanced analysis (recommended)
ANTHROPIC_API_KEY=sk-ant-...
```

Optional API keys for additional news sources:
```
NEWSAPI_KEY=your-key-here
FINNHUB_KEY=your-key-here
```

## Step 4: Verify Configuration

```bash
# Check settings
cat config/settings.yaml

# Check registered sources
cat config/sources.yaml

# Review risk thresholds
cat config/risk_thresholds.yaml
```

## Step 5: Start the System

```bash
./start.sh
```

Verify it's running:
```bash
./status.sh
curl http://localhost:7777/api/v1/health
```

## Step 6: Load Your Portfolio

Upload a CSV file via the API:

```bash
curl -X POST http://localhost:7777/api/v1/portfolio/upload \
  -F "file=@your_portfolio.csv"
```

CSV must contain columns: `ticker`, `quantity`, `price`, `currency`. Optional: `isin`.

Or use the dashboard at `http://localhost:7777/dashboard` and click "Upload Portfolio".

## Step 7: Install as launchd Service (Optional)

For 24/7 operation:

```bash
cp config/launchd/com.axion.core.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.axion.core.plist
```

The service will:
- Start automatically on login
- Restart on crash (KeepAlive)
- Log to `~/kleitos-data/logs/`

## Step 8: Set Up OpenClaw (Optional)

If OpenClaw is installed:

```bash
# Copy the multi-agent config
cp openclaw/openclaw-config.json ~/.openclaw/
cp -r openclaw/workspaces/ ~/.openclaw/workspaces/

# Load the OpenClaw launchd service
cp config/launchd/com.axion.openclaw.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.axion.openclaw.plist
```

## Windows Setup

Double-click `Axion.bat` in the project folder. It will:
- Create a Python virtual environment automatically
- Install all dependencies
- Start the server and open the dashboard

Or manually:
```cmd
cd axion
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn src.main:app --host 127.0.0.1 --port 7777
```

## Uninstall

### macOS
```bash
./stop.sh
launchctl unload ~/Library/LaunchAgents/com.axion.core.plist 2>/dev/null
rm ~/Library/LaunchAgents/com.axion.*.plist
# Data preserved at ~/kleitos-data/ – delete manually if desired
```

### Windows
Stop the server (close the terminal or Ctrl+C), then delete the project folder.
Data is stored in `%USERPROFILE%\kleitos-data\` — delete manually to fully clean up.
