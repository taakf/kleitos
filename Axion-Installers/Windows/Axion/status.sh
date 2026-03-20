#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Axion by 4Labs - Service Status Check
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

KLEITOS_DATA="$HOME/kleitos-data"
DB_PATH="$KLEITOS_DATA/db/kleitos.db"
API_URL="http://localhost:7777/api/v1"

# ---------------------------------------------------------------------------
# API Health
# ---------------------------------------------------------------------------
check_api() {
    header "Axion API"

    local response
    if response=$(curl -sf --max-time 5 "$API_URL/health" 2>/dev/null); then
        printf "    Status:   ${GREEN}HEALTHY${NC}\n"

        # Try to parse JSON response for details
        if command -v python3 &>/dev/null; then
            local version
            version=$(echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version','unknown'))" 2>/dev/null || echo "unknown")
            echo "    Version:  $version"

            local uptime
            uptime=$(echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('uptime','unknown'))" 2>/dev/null || echo "unknown")
            echo "    Uptime:   $uptime"
        fi
    else
        printf "    Status:   ${RED}NOT RESPONDING${NC}\n"
        echo "    URL:      $API_URL/health"
    fi
}

# ---------------------------------------------------------------------------
# OpenClaw
# ---------------------------------------------------------------------------
check_openclaw() {
    header "OpenClaw Gateway"

    if pgrep -f "openclaw" >/dev/null 2>&1; then
        printf "    Status:   ${GREEN}RUNNING${NC}\n"

        # Get PID info
        local pid
        pid=$(pgrep -f "openclaw" | head -1)
        echo "    PID:      $pid"
    else
        printf "    Status:   ${RED}NOT RUNNING${NC}\n"
    fi
}

# ---------------------------------------------------------------------------
# Launchd status
# ---------------------------------------------------------------------------
check_launchd() {
    header "Launchd Services"

    if launchctl list | grep -q "com.kleitos.core" 2>/dev/null; then
        local exit_code
        exit_code=$(launchctl list | grep "com.kleitos.core" | awk '{print $2}')
        if [[ "$exit_code" == "0" || "$exit_code" == "-" ]]; then
            printf "    Axion Core:       ${GREEN}LOADED${NC} (exit: %s)\n" "$exit_code"
        else
            printf "    Axion Core:       ${YELLOW}LOADED${NC} (exit: %s)\n" "$exit_code"
        fi
    else
        printf "    Axion Core:       ${RED}NOT LOADED${NC}\n"
    fi

    if launchctl list | grep -q "com.kleitos.openclaw" 2>/dev/null; then
        local exit_code
        exit_code=$(launchctl list | grep "com.kleitos.openclaw" | awk '{print $2}')
        if [[ "$exit_code" == "0" || "$exit_code" == "-" ]]; then
            printf "    OpenClaw Gateway: ${GREEN}LOADED${NC} (exit: %s)\n" "$exit_code"
        else
            printf "    OpenClaw Gateway: ${YELLOW}LOADED${NC} (exit: %s)\n" "$exit_code"
        fi
    else
        printf "    OpenClaw Gateway: ${RED}NOT LOADED${NC}\n"
    fi
}

# ---------------------------------------------------------------------------
# Collection status
# ---------------------------------------------------------------------------
check_collection() {
    header "Data Collection"

    if [[ ! -f "$DB_PATH" ]]; then
        warn "Database not found at $DB_PATH"
        return
    fi

    # Last collection time
    local last_collection
    last_collection=$(sqlite3 "$DB_PATH" \
        "SELECT MAX(collected_at) FROM collections LIMIT 1;" 2>/dev/null || echo "")

    if [[ -n "$last_collection" && "$last_collection" != "" ]]; then
        printf "    Last collection:  ${GREEN}%s${NC}\n" "$last_collection"
    else
        printf "    Last collection:  ${YELLOW}No collections yet${NC}\n"
    fi

    # Alert count
    local alert_count
    alert_count=$(sqlite3 "$DB_PATH" \
        "SELECT COUNT(*) FROM alerts WHERE resolved = 0;" 2>/dev/null || echo "0")

    if [[ "$alert_count" -gt 0 ]]; then
        printf "    Active alerts:    ${YELLOW}%s${NC}\n" "$alert_count"
    else
        printf "    Active alerts:    ${GREEN}%s${NC}\n" "$alert_count"
    fi
}

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
check_database() {
    header "Database"

    if [[ ! -f "$DB_PATH" ]]; then
        printf "    Status:   ${RED}NOT FOUND${NC}\n"
        echo "    Path:     $DB_PATH"
        return
    fi

    printf "    Status:   ${GREEN}EXISTS${NC}\n"
    echo "    Path:     $DB_PATH"

    # Size
    local size_bytes
    size_bytes=$(stat -f%z "$DB_PATH" 2>/dev/null || echo "0")
    if [[ "$size_bytes" -gt 1073741824 ]]; then
        local size_gb
        size_gb=$(echo "scale=2; $size_bytes / 1073741824" | bc)
        printf "    Size:     ${YELLOW}%s GB${NC}\n" "$size_gb"
    elif [[ "$size_bytes" -gt 1048576 ]]; then
        local size_mb
        size_mb=$(echo "scale=2; $size_bytes / 1048576" | bc)
        echo "    Size:     ${size_mb} MB"
    else
        local size_kb
        size_kb=$(echo "scale=1; $size_bytes / 1024" | bc)
        echo "    Size:     ${size_kb} KB"
    fi

    # Table count
    local table_count
    table_count=$(sqlite3 "$DB_PATH" \
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table';" 2>/dev/null || echo "unknown")
    echo "    Tables:   $table_count"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    printf "\n${BOLD}${BLUE}  Axion Status Report${NC}\n"

    check_api
    check_openclaw
    check_launchd
    check_collection
    check_database

    echo ""
}

main "$@"
