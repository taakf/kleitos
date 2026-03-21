#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Axion by 4Labs - Detailed Health Check
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
LOG_DIR="$KLEITOS_DATA/logs"
API_URL="http://localhost:7777/api/v1"

CHECKS_PASSED=0
CHECKS_WARNED=0
CHECKS_FAILED=0

pass()  { CHECKS_PASSED=$((CHECKS_PASSED + 1)); success "$*"; }
fail()  { CHECKS_FAILED=$((CHECKS_FAILED + 1)); error "$*"; }
alert() { CHECKS_WARNED=$((CHECKS_WARNED + 1)); warn "$*"; }

# ---------------------------------------------------------------------------
# API Health
# ---------------------------------------------------------------------------
check_api_health() {
    header "API Health"

    local response
    local http_code
    http_code=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 10 "$API_URL/health" 2>/dev/null || echo "000")

    if [[ "$http_code" == "200" ]]; then
        pass "API responding (HTTP $http_code)"

        response=$(curl -sf --max-time 10 "$API_URL/health" 2>/dev/null || echo "{}")

        if command -v python3 &>/dev/null; then
            local status
            status=$(echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','unknown'))" 2>/dev/null || echo "unknown")
            if [[ "$status" == "healthy" ]]; then
                pass "API reports healthy status"
            else
                alert "API status: $status"
            fi
        fi
    elif [[ "$http_code" == "000" ]]; then
        fail "API not reachable (connection refused or timeout)"
    else
        fail "API returned HTTP $http_code"
    fi
}

# ---------------------------------------------------------------------------
# Database Integrity
# ---------------------------------------------------------------------------
check_database() {
    header "Database Integrity"

    if [[ ! -f "$DB_PATH" ]]; then
        fail "Database file not found at $DB_PATH"
        return
    fi

    pass "Database file exists"

    # Size check
    local size_bytes
    size_bytes=$(stat -f%z "$DB_PATH" 2>/dev/null || echo "0")

    if [[ "$size_bytes" -eq 0 ]]; then
        fail "Database file is empty"
        return
    fi

    local size_mb
    size_mb=$(echo "scale=2; $size_bytes / 1048576" | bc)
    echo "    Size: ${size_mb} MB"

    if [[ "$size_bytes" -gt 1073741824 ]]; then
        alert "Database is larger than 1 GB (${size_mb} MB)"
    else
        pass "Database size is reasonable (${size_mb} MB)"
    fi

    # Integrity check
    local integrity
    integrity=$(sqlite3 "$DB_PATH" "PRAGMA integrity_check;" 2>/dev/null || echo "error")

    if [[ "$integrity" == "ok" ]]; then
        pass "Database integrity check passed"
    else
        fail "Database integrity check failed: $integrity"
    fi

    # WAL mode check
    local journal_mode
    journal_mode=$(sqlite3 "$DB_PATH" "PRAGMA journal_mode;" 2>/dev/null || echo "unknown")
    echo "    Journal mode: $journal_mode"

    # Table count
    local table_count
    table_count=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';" 2>/dev/null || echo "0")
    echo "    Tables: $table_count"

    if [[ "$table_count" -eq 0 ]]; then
        alert "No tables found in database"
    fi
}

# ---------------------------------------------------------------------------
# Disk Space
# ---------------------------------------------------------------------------
check_disk_space() {
    header "Disk Space"

    # Get available space on the volume containing kleitos-data
    local avail_kb
    avail_kb=$(df -k "$KLEITOS_DATA" 2>/dev/null | tail -1 | awk '{print $4}')

    if [[ -z "$avail_kb" ]]; then
        alert "Could not determine available disk space"
        return
    fi

    local avail_gb
    avail_gb=$(echo "scale=1; $avail_kb / 1048576" | bc)

    if [[ "$avail_kb" -lt 1048576 ]]; then
        fail "Low disk space: ${avail_gb} GB available"
    elif [[ "$avail_kb" -lt 5242880 ]]; then
        alert "Disk space getting low: ${avail_gb} GB available"
    else
        pass "Disk space OK: ${avail_gb} GB available"
    fi
}

# ---------------------------------------------------------------------------
# Log Files
# ---------------------------------------------------------------------------
check_logs() {
    header "Log Files"

    if [[ ! -d "$LOG_DIR" ]]; then
        alert "Log directory not found: $LOG_DIR"
        return
    fi

    local total_log_size=0

    for logfile in "$LOG_DIR"/*.log; do
        if [[ ! -f "$logfile" ]]; then
            continue
        fi

        local name
        name=$(basename "$logfile")
        local size_bytes
        size_bytes=$(stat -f%z "$logfile" 2>/dev/null || echo "0")
        total_log_size=$((total_log_size + size_bytes))

        local size_mb
        size_mb=$(echo "scale=2; $size_bytes / 1048576" | bc)

        if [[ "$size_bytes" -gt 104857600 ]]; then
            alert "$name: ${size_mb} MB (consider rotation)"
        else
            echo "    $name: ${size_mb} MB"
        fi
    done

    local total_mb
    total_mb=$(echo "scale=2; $total_log_size / 1048576" | bc)

    if [[ "$total_log_size" -gt 524288000 ]]; then
        alert "Total log size: ${total_mb} MB (consider cleanup)"
    else
        pass "Total log size: ${total_mb} MB"
    fi

    # Check for recent errors
    local error_log="$LOG_DIR/kleitos-core-error.log"
    if [[ -f "$error_log" ]]; then
        local recent_errors
        recent_errors=$(tail -20 "$error_log" 2>/dev/null | grep -ci "error\|traceback\|exception" || echo "0")
        if [[ "$recent_errors" -gt 0 ]]; then
            alert "Recent errors found in $error_log ($recent_errors in last 20 lines)"
        else
            pass "No recent errors in error log"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Source Health
# ---------------------------------------------------------------------------
check_sources() {
    header "Source Health"

    local response
    if response=$(curl -sf --max-time 10 "$API_URL/sources/health" 2>/dev/null); then
        if command -v python3 &>/dev/null; then
            python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    sources = data if isinstance(data, list) else data.get('sources', [])
    for s in sources:
        name = s.get('name', 'unknown')
        status = s.get('status', 'unknown')
        if status == 'healthy':
            print(f'    {name}: \033[0;32mHEALTHY\033[0m')
        elif status == 'degraded':
            print(f'    {name}: \033[1;33mDEGRADED\033[0m')
        else:
            print(f'    {name}: \033[0;31m{status.upper()}\033[0m')
except:
    print('    Could not parse source health response')
" <<< "$response"
        else
            pass "Sources endpoint responded"
        fi
    else
        alert "Could not reach sources health endpoint ($API_URL/sources/health)"
    fi
}

# ---------------------------------------------------------------------------
# Last Successful Collection
# ---------------------------------------------------------------------------
check_last_collection() {
    header "Last Successful Collection"

    if [[ ! -f "$DB_PATH" ]]; then
        alert "Cannot check - database not found"
        return
    fi

    local last_time
    last_time=$(sqlite3 "$DB_PATH" \
        "SELECT MAX(collected_at) FROM collections WHERE status='success' LIMIT 1;" 2>/dev/null || echo "")

    if [[ -z "$last_time" || "$last_time" == "" ]]; then
        alert "No successful collections recorded"
        return
    fi

    echo "    Last successful: $last_time"

    # Check if it's been more than 24 hours (rough check)
    if command -v python3 &>/dev/null; then
        local hours_ago
        hours_ago=$(python3 -c "
from datetime import datetime, timezone
try:
    last = datetime.fromisoformat('$last_time'.replace('Z','+00:00'))
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    diff = (now - last).total_seconds() / 3600
    print(f'{diff:.1f}')
except:
    print('unknown')
" 2>/dev/null || echo "unknown")

        if [[ "$hours_ago" != "unknown" ]]; then
            local hours_int
            hours_int=$(echo "$hours_ago" | cut -d. -f1)
            if [[ "$hours_int" -gt 24 ]]; then
                alert "Last collection was ${hours_ago} hours ago (>24h)"
            else
                pass "Last collection was ${hours_ago} hours ago"
            fi
        fi
    fi
}

# ---------------------------------------------------------------------------
# OpenClaw Connectivity
# ---------------------------------------------------------------------------
check_openclaw() {
    header "OpenClaw Connectivity"

    if pgrep -f "openclaw" >/dev/null 2>&1; then
        pass "OpenClaw process is running"
    else
        fail "OpenClaw process is not running"
        return
    fi

    # Try to reach OpenClaw (typically port 3000)
    if curl -sf --max-time 5 "http://localhost:3000/health" >/dev/null 2>&1; then
        pass "OpenClaw health endpoint responding"
    elif curl -sf --max-time 5 "http://localhost:3000/" >/dev/null 2>&1; then
        pass "OpenClaw responding on port 3000"
    else
        alert "OpenClaw process running but not responding on port 3000"
    fi
}

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
print_report() {
    header "Health Check Summary"

    local total=$((CHECKS_PASSED + CHECKS_WARNED + CHECKS_FAILED))

    printf "    ${GREEN}Passed:${NC}   %d\n" "$CHECKS_PASSED"
    printf "    ${YELLOW}Warnings:${NC} %d\n" "$CHECKS_WARNED"
    printf "    ${RED}Failed:${NC}   %d\n" "$CHECKS_FAILED"
    printf "    Total:    %d\n" "$total"
    echo ""

    if [[ $CHECKS_FAILED -gt 0 ]]; then
        printf "    Overall: ${RED}${BOLD}UNHEALTHY${NC}\n"
    elif [[ $CHECKS_WARNED -gt 0 ]]; then
        printf "    Overall: ${YELLOW}${BOLD}DEGRADED${NC}\n"
    else
        printf "    Overall: ${GREEN}${BOLD}HEALTHY${NC}\n"
    fi

    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    printf "\n${BOLD}${BLUE}  Axion Detailed Health Check${NC}\n"

    check_api_health
    check_database
    check_disk_space
    check_logs
    check_sources
    check_last_collection
    check_openclaw
    print_report
}

main "$@"
