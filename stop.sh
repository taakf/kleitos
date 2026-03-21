#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Axion by 4Labs - Stop All Services
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

LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST_CORE="com.kleitos.core.plist"
PLIST_OPENCLAW="com.kleitos.openclaw.plist"

# ---------------------------------------------------------------------------
# Stop Axion Core
# ---------------------------------------------------------------------------
stop_core() {
    header "Stopping Axion Core"

    local plist_path="$LAUNCH_AGENTS/$PLIST_CORE"

    if launchctl list | grep -q "com.kleitos.core" 2>/dev/null; then
        info "Unloading Axion core service..."
        launchctl unload "$plist_path" 2>/dev/null || true
        success "Axion core service stopped"
    else
        info "Axion core service was not loaded"
    fi
}

# ---------------------------------------------------------------------------
# Stop OpenClaw Gateway
# ---------------------------------------------------------------------------
stop_openclaw() {
    header "Stopping OpenClaw Gateway"

    local plist_path="$LAUNCH_AGENTS/$PLIST_OPENCLAW"

    if launchctl list | grep -q "com.kleitos.openclaw" 2>/dev/null; then
        info "Unloading OpenClaw gateway service..."
        launchctl unload "$plist_path" 2>/dev/null || true
        success "OpenClaw gateway service stopped"
    else
        info "OpenClaw gateway service was not loaded"
    fi

    # Kill any remaining openclaw processes
    if pgrep -f "openclaw" >/dev/null 2>&1; then
        warn "Found lingering OpenClaw processes. Terminating..."
        pkill -f "openclaw" 2>/dev/null || true
        sleep 1
        success "Lingering processes terminated"
    fi
}

# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------
confirm_stopped() {
    header "Verification"

    local all_stopped=true

    if launchctl list | grep -q "com.kleitos.core" 2>/dev/null; then
        printf "    Axion Core:       ${RED}STILL RUNNING${NC}\n"
        all_stopped=false
    else
        printf "    Axion Core:       ${GREEN}STOPPED${NC}\n"
    fi

    if launchctl list | grep -q "com.kleitos.openclaw" 2>/dev/null; then
        printf "    OpenClaw Gateway: ${RED}STILL RUNNING${NC}\n"
        all_stopped=false
    else
        printf "    OpenClaw Gateway: ${GREEN}STOPPED${NC}\n"
    fi

    echo ""

    if $all_stopped; then
        success "All services stopped successfully."
    else
        error "Some services may still be running. Check 'launchctl list | grep axion'."
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    printf "\n${BOLD}${BLUE}  Stopping Axion Services${NC}\n"

    stop_core
    stop_openclaw
    confirm_stopped
}

main "$@"
