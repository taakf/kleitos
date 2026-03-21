#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Axion by 4Labs - Restore from Backup
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
KLEITOS_DATA="$HOME/kleitos-data"
DB_PATH="$KLEITOS_DATA/db/kleitos.db"
BACKUP_DIR="$KLEITOS_DATA/backups"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    echo ""
    echo "  Usage: $0 <backup-file>"
    echo ""
    echo "  Restore the Axion database from a backup file."
    echo ""
    echo "  Arguments:"
    echo "    backup-file   Path to the backup .db file, or just the filename"
    echo "                  if it's in $BACKUP_DIR"
    echo ""
    echo "  Available backups:"

    if [[ -d "$BACKUP_DIR" ]]; then
        find "$BACKUP_DIR" -name "kleitos_*.db" -type f 2>/dev/null \
            | sort -r \
            | while read -r f; do
                local name
                name=$(basename "$f")
                local size
                size=$(stat -f%z "$f" 2>/dev/null || echo "0")
                local size_mb
                size_mb=$(echo "scale=2; $size / 1048576" | bc)
                echo "    $name  (${size_mb} MB)"
            done
    else
        echo "    (no backup directory found)"
    fi

    echo ""
    exit 1
}

# ---------------------------------------------------------------------------
# Validate backup
# ---------------------------------------------------------------------------
validate_backup() {
    local backup_file="$1"

    header "Validating Backup"

    if [[ ! -f "$backup_file" ]]; then
        error "Backup file not found: $backup_file"
        exit 1
    fi

    info "Backup file: $backup_file"

    local size_bytes
    size_bytes=$(stat -f%z "$backup_file" 2>/dev/null || echo "0")
    local size_mb
    size_mb=$(echo "scale=2; $size_bytes / 1048576" | bc)
    info "Size: ${size_mb} MB"

    if [[ "$size_bytes" -eq 0 ]]; then
        error "Backup file is empty"
        exit 1
    fi

    # Check integrity
    info "Checking backup integrity..."
    local integrity
    integrity=$(sqlite3 "$backup_file" "PRAGMA integrity_check;" 2>/dev/null || echo "error")

    if [[ "$integrity" == "ok" ]]; then
        success "Backup integrity verified"
    else
        error "Backup integrity check failed: $integrity"
        error "This backup may be corrupted."
        exit 1
    fi

    # Show some info about the backup
    local table_count
    table_count=$(sqlite3 "$backup_file" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';" 2>/dev/null || echo "unknown")
    info "Tables in backup: $table_count"
}

# ---------------------------------------------------------------------------
# Stop services
# ---------------------------------------------------------------------------
stop_services() {
    header "Stopping Services"

    if [[ -f "$KLEITOS_HOME/stop.sh" ]]; then
        bash "$KLEITOS_HOME/stop.sh"
    else
        launchctl unload "$HOME/Library/LaunchAgents/com.kleitos.core.plist" 2>/dev/null || true
        launchctl unload "$HOME/Library/LaunchAgents/com.kleitos.openclaw.plist" 2>/dev/null || true
        success "Services stopped"
    fi

    # Give processes time to release file handles
    sleep 2
}

# ---------------------------------------------------------------------------
# Replace database
# ---------------------------------------------------------------------------
replace_database() {
    local backup_file="$1"

    header "Replacing Database"

    # Create a safety backup of the current database
    if [[ -f "$DB_PATH" ]]; then
        local timestamp
        timestamp=$(date +%Y%m%d_%H%M%S)
        local safety_backup="$BACKUP_DIR/kleitos_pre_restore_${timestamp}.db"
        mkdir -p "$BACKUP_DIR"

        info "Creating safety backup of current database..."
        cp "$DB_PATH" "$safety_backup"
        success "Safety backup: $safety_backup"

        info "Removing current database..."
        rm -f "$DB_PATH"
        # Also remove WAL and SHM files
        rm -f "${DB_PATH}-wal" "${DB_PATH}-shm"
    fi

    info "Restoring from backup..."
    cp "$backup_file" "$DB_PATH"

    if [[ -f "$DB_PATH" ]]; then
        success "Database restored"
    else
        error "Database restoration failed"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Restart services
# ---------------------------------------------------------------------------
restart_services() {
    header "Restarting Services"

    if [[ -f "$KLEITOS_HOME/start.sh" ]]; then
        bash "$KLEITOS_HOME/start.sh"
    else
        launchctl load "$HOME/Library/LaunchAgents/com.kleitos.core.plist" 2>/dev/null || true
        launchctl load "$HOME/Library/LaunchAgents/com.kleitos.openclaw.plist" 2>/dev/null || true
        success "Services started"
    fi
}

# ---------------------------------------------------------------------------
# Verify restoration
# ---------------------------------------------------------------------------
verify_restoration() {
    header "Verification"

    # Check database file
    if [[ ! -f "$DB_PATH" ]]; then
        error "Database file not present after restore"
        exit 1
    fi
    success "Database file exists"

    # Check integrity
    local integrity
    integrity=$(sqlite3 "$DB_PATH" "PRAGMA integrity_check;" 2>/dev/null || echo "error")
    if [[ "$integrity" == "ok" ]]; then
        success "Restored database integrity verified"
    else
        error "Restored database has integrity issues: $integrity"
    fi

    # Check size
    local size_bytes
    size_bytes=$(stat -f%z "$DB_PATH" 2>/dev/null || echo "0")
    local size_mb
    size_mb=$(echo "scale=2; $size_bytes / 1048576" | bc)
    info "Database size: ${size_mb} MB"

    # Check table count
    local table_count
    table_count=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';" 2>/dev/null || echo "0")
    info "Tables: $table_count"

    # Wait for API and check
    info "Waiting for API to come online..."
    sleep 5

    if curl -sf --max-time 5 "http://localhost:7777/api/v1/health" >/dev/null 2>&1; then
        success "API is healthy after restoration"
    else
        warn "API not yet responding. It may still be starting up."
        warn "Check: curl http://localhost:7777/api/v1/health"
    fi

    echo ""
    success "Restoration complete!"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    printf "\n${BOLD}${BLUE}  Axion Database Restore${NC}\n"

    if [[ $# -lt 1 ]]; then
        usage
    fi

    local backup_file="$1"

    # If just a filename was given, look in the backup directory
    if [[ ! -f "$backup_file" && -f "$BACKUP_DIR/$backup_file" ]]; then
        backup_file="$BACKUP_DIR/$backup_file"
    fi

    validate_backup "$backup_file"

    # Confirmation prompt
    echo ""
    printf "  ${YELLOW}${BOLD}WARNING:${NC} This will replace the current database with the backup.\n"
    printf "  A safety backup of the current database will be created first.\n\n"
    printf "  Continue? [y/N] "
    read -r confirm

    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        info "Restore cancelled."
        exit 0
    fi

    stop_services
    replace_database "$backup_file"
    restart_services
    verify_restoration
}

main "$@"
