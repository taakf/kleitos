#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Axion by 4Labs - Start All Services
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

info()    { printf "${BLUE}[INFO]${NC}  %s\n" "$*"; }
success() { printf "${GREEN}[OK]${NC}    %s\n" "$*"; }
warn()    { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
error()   { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; }
header()  { printf "\n${BOLD}=== %s ===${NC}\n\n" "$*"; }

KLEITOS_HOME="$HOME/kleitos"
VENV_DIR="$KLEITOS_HOME/venv"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST_CORE="com.kleitos.core.plist"
PLIST_OPENCLAW="com.kleitos.openclaw.plist"

# ---------------------------------------------------------------------------
# Activate virtual environment
# ---------------------------------------------------------------------------
activate_venv() {
    if [[ -d "$VENV_DIR" ]]; then
        # shellcheck disable=SC1091
        source "$VENV_DIR/bin/activate"
        success "Virtual environment activated"
    else
        error "Virtual environment not found at $VENV_DIR"
        error "Run install.sh first."
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Start Axion Core
# ---------------------------------------------------------------------------
start_core() {
    header "Starting Axion Core"

    local plist_path="$LAUNCH_AGENTS/$PLIST_CORE"
    if [[ ! -f "$plist_path" ]]; then
        error "Launchd plist not found: $plist_path"
        error "Run install.sh first."
        exit 1
    fi

    # Check if already loaded
    if launchctl list | grep -q "com.kleitos.core" 2>/dev/null; then
        warn "Axion core is already loaded. Unloading first..."
        launchctl unload "$plist_path" 2>/dev/null || true
    fi

    info "Loading Axion core service..."
    launchctl load "$plist_path"
    success "Axion core service loaded"
}

# ---------------------------------------------------------------------------
# Start OpenClaw Gateway
# ---------------------------------------------------------------------------
start_openclaw() {
    header "Starting OpenClaw Gateway"

    local plist_path="$LAUNCH_AGENTS/$PLIST_OPENCLAW"
    if [[ ! -f "$plist_path" ]]; then
        error "Launchd plist not found: $plist_path"
        error "Run install.sh first."
        exit 1
    fi

    # Check if already loaded
    if launchctl list | grep -q "com.kleitos.openclaw" 2>/dev/null; then
        warn "OpenClaw gateway is already loaded. Unloading first..."
        launchctl unload "$plist_path" 2>/dev/null || true
    fi

    info "Loading OpenClaw gateway service..."
    launchctl load "$plist_path"
    success "OpenClaw gateway service loaded"
}

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
wait_and_check() {
    header "Health Check"

    info "Waiting for services to start..."
    sleep 3

    # Check Axion Core
    local retries=5
    local healthy=false
    while [[ $retries -gt 0 ]]; do
        if curl -sf "http://localhost:7777/api/v1/health" >/dev/null 2>&1; then
            healthy=true
            break
        fi
        retries=$((retries - 1))
        sleep 2
    done

    if $healthy; then
        success "Axion API is healthy (http://localhost:7777)"
    else
        warn "Axion API not responding yet. It may still be starting up."
        warn "Check logs at ~/kleitos-data/logs/kleitos-core.log"
    fi

    # Check OpenClaw
    if pgrep -f "openclaw" >/dev/null 2>&1; then
        success "OpenClaw gateway is running"
    else
        warn "OpenClaw gateway not detected. Check logs at ~/kleitos-data/logs/openclaw.log"
    fi
}

# ---------------------------------------------------------------------------
# Print status
# ---------------------------------------------------------------------------
print_status() {
    header "Service Status"

    echo "  Services:"
    if launchctl list | grep -q "com.kleitos.core" 2>/dev/null; then
        printf "    Axion Core:       ${GREEN}LOADED${NC}\n"
    else
        printf "    Axion Core:       ${RED}NOT LOADED${NC}\n"
    fi

    if launchctl list | grep -q "com.kleitos.openclaw" 2>/dev/null; then
        printf "    OpenClaw Gateway: ${GREEN}LOADED${NC}\n"
    else
        printf "    OpenClaw Gateway: ${RED}NOT LOADED${NC}\n"
    fi

    echo ""
    echo "  Access URLs:"
    printf "    Axion API:        ${BLUE}http://localhost:7777${NC}\n"
    printf "    API Health:       ${BLUE}http://localhost:7777/api/v1/health${NC}\n"
    printf "    API Docs:         ${BLUE}http://localhost:7777/docs${NC}\n"
    echo ""
    echo "  Logs:"
    echo "    Axion:    ~/kleitos-data/logs/kleitos-core.log"
    echo "    OpenClaw: ~/kleitos-data/logs/openclaw.log"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    printf "\n${BOLD}${BLUE}  Starting Axion Services${NC}\n"

    activate_venv
    start_core
    start_openclaw
    wait_and_check
    print_status
}

main "$@"
