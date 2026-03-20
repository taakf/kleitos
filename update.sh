#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Axion by 4Labs - System Update
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
VENV_DIR="$KLEITOS_HOME/venv"
DB_PATH="$KLEITOS_DATA/db/kleitos.db"
BACKUP_DIR="$KLEITOS_DATA/backups"

# ---------------------------------------------------------------------------
# Stop services
# ---------------------------------------------------------------------------
stop_services() {
    header "Stopping Services"

    if [[ -f "$KLEITOS_HOME/stop.sh" ]]; then
        bash "$KLEITOS_HOME/stop.sh"
    else
        warn "stop.sh not found. Attempting manual stop..."
        launchctl unload "$HOME/Library/LaunchAgents/com.kleitos.core.plist" 2>/dev/null || true
        launchctl unload "$HOME/Library/LaunchAgents/com.kleitos.openclaw.plist" 2>/dev/null || true
        success "Services stopped"
    fi
}

# ---------------------------------------------------------------------------
# Backup database
# ---------------------------------------------------------------------------
backup_database() {
    header "Backing Up Database"

    if [[ ! -f "$DB_PATH" ]]; then
        warn "Database not found at $DB_PATH, skipping backup."
        return
    fi

    mkdir -p "$BACKUP_DIR"
    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)
    local backup_file="$BACKUP_DIR/kleitos_pre_update_${timestamp}.db"

    info "Backing up database to $backup_file..."
    cp "$DB_PATH" "$backup_file"

    local size
    size=$(stat -f%z "$backup_file" 2>/dev/null || echo "unknown")
    success "Backup created (${size} bytes)"
}

# ---------------------------------------------------------------------------
# Pull latest code
# ---------------------------------------------------------------------------
pull_code() {
    header "Pulling Latest Code"

    cd "$KLEITOS_HOME"

    if [[ ! -d ".git" ]]; then
        warn "Not a git repository. Skipping code pull."
        return
    fi

    # Check for local changes
    if ! git diff --quiet 2>/dev/null; then
        warn "Local changes detected. Stashing..."
        git stash
        success "Changes stashed"
    fi

    info "Pulling latest changes..."
    local current_hash
    current_hash=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

    git pull --rebase origin main 2>/dev/null || git pull --rebase 2>/dev/null || {
        warn "Git pull failed. Continuing with current code."
        return
    }

    local new_hash
    new_hash=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

    if [[ "$current_hash" == "$new_hash" ]]; then
        info "Already up to date ($current_hash)"
    else
        success "Updated from $current_hash to $new_hash"
    fi
}

# ---------------------------------------------------------------------------
# Update dependencies
# ---------------------------------------------------------------------------
update_dependencies() {
    header "Updating Python Dependencies"

    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"

    info "Upgrading pip..."
    pip install --upgrade pip --quiet

    if [[ -f "$KLEITOS_HOME/requirements.txt" ]]; then
        info "Installing updated dependencies..."
        pip install -r "$KLEITOS_HOME/requirements.txt" --upgrade --quiet
        success "Python dependencies updated"
    else
        warn "requirements.txt not found"
    fi
}

# ---------------------------------------------------------------------------
# Run migrations
# ---------------------------------------------------------------------------
run_migrations() {
    header "Database Migrations"

    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"

    if [[ -f "$KLEITOS_HOME/scripts/migrate.py" ]]; then
        info "Running database migrations..."
        python "$KLEITOS_HOME/scripts/migrate.py"
        success "Migrations complete"
    elif python -c "from kleitos.db import migrate; migrate()" 2>/dev/null; then
        success "Migrations complete (via module)"
    elif [[ -d "$KLEITOS_HOME/alembic" ]]; then
        info "Running Alembic migrations..."
        cd "$KLEITOS_HOME"
        alembic upgrade head
        success "Alembic migrations complete"
    else
        info "No migration system detected. Skipping."
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
        warn "start.sh not found. Attempting manual start..."
        launchctl load "$HOME/Library/LaunchAgents/com.kleitos.core.plist" 2>/dev/null || true
        launchctl load "$HOME/Library/LaunchAgents/com.kleitos.openclaw.plist" 2>/dev/null || true
        success "Services started"
    fi
}

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
health_check() {
    header "Post-Update Health Check"

    info "Waiting for services to initialize..."
    sleep 5

    local retries=5
    local healthy=false
    while [[ $retries -gt 0 ]]; do
        if curl -sf --max-time 5 "http://localhost:7777/api/v1/health" >/dev/null 2>&1; then
            healthy=true
            break
        fi
        retries=$((retries - 1))
        sleep 2
    done

    if $healthy; then
        success "Axion API is healthy"
    else
        error "Axion API is not responding."
        error "Check logs: tail -f ~/kleitos-data/logs/kleitos-core.log"
        error "You may need to restore from backup and investigate."
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    printf "\n${BOLD}${BLUE}  Axion System Update${NC}\n"

    stop_services
    backup_database
    pull_code
    update_dependencies
    run_migrations
    restart_services
    health_check

    echo ""
    success "Update complete!"
    echo ""
}

main "$@"
