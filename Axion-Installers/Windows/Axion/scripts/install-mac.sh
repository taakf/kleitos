#!/bin/bash
# ============================================================================
# Axion by 4Labs — Mac Mini Installer (Standalone, No Docker)
#
# NOTE: This is OPTIONAL. Just double-click Axion.app and everything
# sets itself up automatically. This script is for advanced users who
# prefer a terminal-based install.
#
# Run on the client's Mac Mini:
#   cd /path/to/axion
#   chmod +x scripts/install-mac.sh
#   ./scripts/install-mac.sh
#
# What it does:
#   1. Installs Python 3.12 via Homebrew (if needed)
#   2. Creates a self-contained virtual environment
#   3. Installs all dependencies
#   4. Creates data directories
#   5. Installs Axion.app to /Applications
#   6. Sets up auto-start on boot (launchd)
#   7. Starts Axion and opens the dashboard
# ============================================================================

set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

APP_NAME="Axion"
PORT="${KLEITOS_PORT:-7777}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${HOME}/kleitos-data"
VENV_DIR="${PROJECT_DIR}/.venv"
PLIST_NAME="com.axion.app"
PLIST_PATH="${HOME}/Library/LaunchAgents/${PLIST_NAME}.plist"

info()  { echo -e "${GREEN}[+]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[!]${RESET} $*"; }
fail()  { echo -e "${RED}[x]${RESET} $*"; exit 1; }
step()  { echo -e "\n${BOLD}--- $* ---${RESET}"; }

echo -e "${BOLD}"
echo "     _          _              "
echo "    / \   __  _(_) ___  _ __  "
echo "   / _ \  \ \/ / |/ _ \| '_ \ "
echo "  / ___ \  >  <| | (_) | | | |"
echo " /_/   \_\/_/\_\_|\___/|_| |_|"
echo "                               "
echo -e "${RESET}"
echo -e "${BOLD}Axion by 4Labs — Portfolio Intelligence System — Installer${RESET}"
echo ""

# --------------------------------------------------------------------------
step "1/7  Checking prerequisites"
# --------------------------------------------------------------------------

# Check macOS
if [[ "$(uname)" != "Darwin" ]]; then
    fail "This installer is for macOS only."
fi

# Check/install Homebrew
if ! command -v brew &>/dev/null; then
    info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add Homebrew to PATH for Apple Silicon
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
fi
info "Homebrew ready"

# Check/install Python 3.12
PYTHON=""
for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        ver=$($candidate --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    info "Installing Python 3.12 via Homebrew..."
    brew install python@3.12
    PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
fi
info "Python: $($PYTHON --version)"

# --------------------------------------------------------------------------
step "2/7  Creating virtual environment"
# --------------------------------------------------------------------------

if [[ -d "${VENV_DIR}" ]]; then
    info "Virtual environment already exists, updating..."
else
    $PYTHON -m venv "${VENV_DIR}"
    info "Created virtual environment at ${VENV_DIR}"
fi

# Activate
source "${VENV_DIR}/bin/activate"

# Upgrade pip
pip install --upgrade pip -q

# --------------------------------------------------------------------------
step "3/7  Installing dependencies"
# --------------------------------------------------------------------------

pip install -r "${PROJECT_DIR}/requirements.txt" -q
info "All Python packages installed"

# Verify critical imports
python -c "import fastapi, uvicorn, sqlalchemy, aiosqlite, apscheduler; print('All imports OK')"

# --------------------------------------------------------------------------
step "4/7  Creating data directories"
# --------------------------------------------------------------------------

mkdir -p "${DATA_DIR}/db"
mkdir -p "${DATA_DIR}/logs"
mkdir -p "${DATA_DIR}/backups"
info "Data directory: ${DATA_DIR}"

# --------------------------------------------------------------------------
step "5/7  Generating configuration"
# --------------------------------------------------------------------------

ENV_FILE="${PROJECT_DIR}/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
    cat > "${ENV_FILE}" <<'ENVEOF'
# Axion by 4Labs - Environment Configuration
# -----------------------------------
# Anthropic API key (optional — system works without it using rule-based fallbacks)
# ANTHROPIC_API_KEY=sk-ant-...

# NewsAPI key (optional — for news collection from newsapi.org)
# NEWSAPI_KEY=...
ENVEOF
    info "Created .env file — edit to add API keys (optional)"
else
    info ".env already exists, keeping it"
fi

# --------------------------------------------------------------------------
step "6/7  Installing Axion.app to /Applications"
# --------------------------------------------------------------------------

APP_SRC="${PROJECT_DIR}/Axion.app"
APP_DST="/Applications/Axion.app"

if [[ -d "${APP_SRC}" ]]; then
    chmod +x "${APP_SRC}/Contents/MacOS/axion-launcher"

    # Write project dir to a well-known file so the launcher can find it
    # (works even when .app is copied to /Applications)
    echo "${PROJECT_DIR}" > "${HOME}/.axion-project-dir"

    # Copy to /Applications
    rm -rf "${APP_DST}" 2>/dev/null || true
    cp -R "${APP_SRC}" "${APP_DST}"

    # Ad-hoc sign the bundle so Finder launch does not SIGKILL it
    codesign --force --deep --sign - "${APP_DST}" 2>/dev/null || true
    info "Installed /Applications/Axion.app"
else
    warn "Axion.app bundle not found, skipping"
fi

# --------------------------------------------------------------------------
step "7/7  Setting up auto-start on boot"
# --------------------------------------------------------------------------

mkdir -p "${HOME}/Library/LaunchAgents"

# Determine correct Python path inside venv
VENV_PYTHON="${VENV_DIR}/bin/python"

cat > "${PLIST_PATH}" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV_PYTHON}</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>src.main:app</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>${PORT}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${DATA_DIR}/logs/kleitos-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${DATA_DIR}/logs/kleitos-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
        <key>KLEITOS_DATA_DIR</key>
        <string>${DATA_DIR}</string>
        <key>KLEITOS_DB_PATH</key>
        <string>${DATA_DIR}/db/kleitos.db</string>
    </dict>
</dict>
</plist>
PLISTEOF

# Load (or reload) the plist
launchctl unload "${PLIST_PATH}" 2>/dev/null || true
launchctl load "${PLIST_PATH}"
info "Auto-start configured — Axion starts on boot and restarts if it crashes"

# --------------------------------------------------------------------------
# Wait for startup
# --------------------------------------------------------------------------
echo ""
info "Starting Axion..."

waited=0
while true; do
    code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${PORT}/api/v1/health" 2>/dev/null || echo "000")
    if [[ "${code}" == "200" ]]; then
        break
    fi
    sleep 1
    waited=$((waited + 1))
    if [[ $waited -ge 45 ]]; then
        fail "Axion did not start within 45 seconds. Check ${DATA_DIR}/logs/"
    fi
done

# Open dashboard
open "http://localhost:${PORT}"

# --------------------------------------------------------------------------
echo ""
echo -e "${BOLD}============================================${RESET}"
echo -e "${GREEN}${BOLD}  Axion installed successfully!${RESET}"
echo -e "${BOLD}============================================${RESET}"
echo ""
echo -e "  Dashboard  :  ${GREEN}http://localhost:${PORT}${RESET}"
echo -e "  App        :  /Applications/Axion.app"
echo -e "  Data       :  ${DATA_DIR}"
echo -e "  Logs       :  ${DATA_DIR}/logs"
echo -e "  Config     :  ${PROJECT_DIR}/.env"
echo ""
echo -e "  ${BOLD}Auto-start${RESET}  :  Starts on boot, restarts on crash"
echo -e "  ${BOLD}Open app${RESET}    :  Double-click Axion in Applications"
echo -e "                or Spotlight search \"Axion\""
echo ""
echo -e "  To add an Anthropic API key for AI-powered analysis:"
echo -e "    echo 'ANTHROPIC_API_KEY=sk-ant-...' >> ${PROJECT_DIR}/.env"
echo -e "    launchctl kickstart -k gui/\$(id -u)/com.kleitos.app"
echo ""
echo -e "  ${BOLD}No Docker required. No terminal required after this.${RESET}"
echo ""
