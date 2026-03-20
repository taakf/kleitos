#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Axion by 4Labs - Manual Database Backup
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
BACKUP_DIR="$KLEITOS_DATA/backups"
MAX_BACKUPS=7

# ---------------------------------------------------------------------------
# Create backup
# ---------------------------------------------------------------------------
create_backup() {
    header "Creating Backup"

    if [[ ! -f "$DB_PATH" ]]; then
        error "Database not found at $DB_PATH"
        exit 1
    fi

    mkdir -p "$BACKUP_DIR"

    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)
    local backup_file="$BACKUP_DIR/kleitos_${timestamp}.db"

    info "Source: $DB_PATH"
    info "Target: $backup_file"

    # Use sqlite3 backup command for a safe copy (handles WAL mode)
    if command -v sqlite3 &>/dev/null; then
        sqlite3 "$DB_PATH" ".backup '$backup_file'"
    else
        cp "$DB_PATH" "$backup_file"
    fi

    if [[ ! -f "$backup_file" ]]; then
        error "Backup file was not created"
        exit 1
    fi

    local size_bytes
    size_bytes=$(stat -f%z "$backup_file" 2>/dev/null || echo "0")
    local size_mb
    size_mb=$(echo "scale=2; $size_bytes / 1048576" | bc)

    success "Backup created: $backup_file (${size_mb} MB)"

    # Verify backup integrity
    info "Verifying backup integrity..."
    local integrity
    integrity=$(sqlite3 "$backup_file" "PRAGMA integrity_check;" 2>/dev/null || echo "error")
    if [[ "$integrity" == "ok" ]]; then
        success "Backup integrity verified"
    else
        error "Backup integrity check failed: $integrity"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Cleanup old backups
# ---------------------------------------------------------------------------
cleanup_old_backups() {
    header "Cleanup"

    local backup_count
    backup_count=$(find "$BACKUP_DIR" -name "kleitos_*.db" -type f 2>/dev/null | wc -l | tr -d ' ')

    info "Found $backup_count backup(s) (keeping last $MAX_BACKUPS)"

    if [[ "$backup_count" -le "$MAX_BACKUPS" ]]; then
        info "No cleanup needed"
        return
    fi

    local to_remove
    to_remove=$((backup_count - MAX_BACKUPS))
    info "Removing $to_remove old backup(s)..."

    # List files sorted by name (timestamp-based), remove oldest
    find "$BACKUP_DIR" -name "kleitos_*.db" -type f 2>/dev/null \
        | sort \
        | head -n "$to_remove" \
        | while read -r old_backup; do
            rm -f "$old_backup"
            info "Removed: $(basename "$old_backup")"
        done

    success "Cleanup complete"
}

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
print_report() {
    header "Backup Summary"

    echo "  Current backups:"
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

    echo ""
    echo "  Backup directory: $BACKUP_DIR"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    printf "\n${BOLD}${BLUE}  Axion Database Backup${NC}\n"

    create_backup
    cleanup_old_backups
    print_report
}

main "$@"
