#!/bin/bash
# ============================================================================
# Axion by 4Labs — Mac Mini Uninstaller
#
# Stops the service, removes the .app, disables auto-start.
# Does NOT delete data — remove ~/kleitos-data manually if desired.
#
# Usage:
#   chmod +x scripts/uninstall-mac.sh
#   ./scripts/uninstall-mac.sh
# ============================================================================

set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RESET="\033[0m"

PORT="${KLEITOS_PORT:-7777}"
PLIST_NAME="com.axion.app"
PLIST_PATH="${HOME}/Library/LaunchAgents/${PLIST_NAME}.plist"
PLIST_LEGACY="${HOME}/Library/LaunchAgents/com.kleitos.app.plist"
DATA_DIR="${HOME}/kleitos-data"

info()  { echo -e "${GREEN}[+]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[!]${RESET} $*"; }

echo ""
read -p "Uninstall Axion? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi
echo ""
echo -e "${BOLD}Uninstalling Axion...${RESET}"
echo ""

# 1. Stop the service
for plist_file in "${PLIST_PATH}" "${PLIST_LEGACY}"; do
    if [[ -f "${plist_file}" ]]; then
        launchctl unload "${plist_file}" 2>/dev/null || true
        rm -f "${plist_file}"
        info "Stopped service and removed auto-start: $(basename "${plist_file}")"
    fi
done

# 2. Kill any running process
if [[ -f "${DATA_DIR}/kleitos.pid" ]]; then
    PID=$(cat "${DATA_DIR}/kleitos.pid" 2>/dev/null || echo "")
    if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null || true
        info "Stopped Axion process (PID ${PID})"
    fi
    rm -f "${DATA_DIR}/kleitos.pid"
fi

# Also check for any stray uvicorn processes (narrow pattern with port)
pkill -f "uvicorn src.main:app.*--port ${PORT}" 2>/dev/null || true

# 3. Remove .app from /Applications
if [[ -d "/Applications/Axion.app" ]]; then
    rm -rf "/Applications/Axion.app"
    info "Removed /Applications/Axion.app"
fi
# Also remove legacy Kleitos.app if present
if [[ -d "/Applications/Kleitos.app" ]]; then
    rm -rf "/Applications/Kleitos.app"
    info "Removed legacy /Applications/Kleitos.app"
fi

# 4. Clean up old plist name and project dir pointer
OLD_PLIST="${HOME}/Library/LaunchAgents/com.kleitos.api.plist"
if [[ -f "${OLD_PLIST}" ]]; then
    launchctl unload "${OLD_PLIST}" 2>/dev/null || true
    rm -f "${OLD_PLIST}"
    info "Removed old launchd plist"
fi
rm -f "${HOME}/.axion-project-dir" "${HOME}/.kleitos-project-dir" 2>/dev/null || true

echo ""
echo -e "${GREEN}Axion uninstalled.${RESET}"
echo ""
echo "Your data is still at ~/kleitos-data/"
echo "  Database : ~/kleitos-data/db/kleitos.db"
echo "  Logs     : ~/kleitos-data/logs/"
echo "  Backups  : ~/kleitos-data/backups/"
echo ""
echo "To remove all data:  rm -rf ~/kleitos-data"
echo "To remove the venv:  rm -rf /path/to/axion/.venv"
echo ""
