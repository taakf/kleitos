#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Axion by 4Labs - Interactive Configuration Wizard
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
KLEITOS_ENV="$HOME/.kleitos.env"
VENV_DIR="$KLEITOS_HOME/venv"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
set_env_value() {
    local key="$1"
    local value="$2"
    local env_file="$KLEITOS_ENV"

    if grep -q "^${key}=" "$env_file" 2>/dev/null; then
        # Replace existing value (macOS-compatible sed)
        sed -i '' "s|^${key}=.*|${key}=${value}|" "$env_file"
    else
        echo "${key}=${value}" >> "$env_file"
    fi
}

prompt_key() {
    local prompt_text="$1"
    local key_name="$2"
    local required="${3:-false}"
    local value=""

    while true; do
        printf "${BOLD}%s${NC}" "$prompt_text"
        read -r value

        if [[ -z "$value" && "$required" == "true" ]]; then
            error "This field is required. Please enter a value."
            continue
        fi

        break
    done

    if [[ -n "$value" ]]; then
        set_env_value "$key_name" "$value"
        success "Set $key_name"
    else
        info "Skipped $key_name (left unchanged)"
    fi
}

# ---------------------------------------------------------------------------
# API Key Configuration
# ---------------------------------------------------------------------------
configure_api_keys() {
    header "API Key Configuration"

    echo "  Please provide your API keys. Required keys are marked with (*)."
    echo ""

    # Anthropic API Key (required)
    local current_anthropic=""
    if [[ -f "$KLEITOS_ENV" ]]; then
        current_anthropic="$(grep '^ANTHROPIC_API_KEY=' "$KLEITOS_ENV" 2>/dev/null | cut -d= -f2- || true)"
    fi

    if [[ -n "$current_anthropic" && "$current_anthropic" != "" ]]; then
        local masked="${current_anthropic: -4}"
        printf "  Current Anthropic API key: ****%s\n\n" "$masked"
        printf "  Enter new Anthropic API key (press Enter to keep current): "
        read -r new_key
        if [[ -n "$new_key" ]]; then
            set_env_value "ANTHROPIC_API_KEY" "$new_key"
            success "Updated ANTHROPIC_API_KEY"
        else
            info "Kept existing ANTHROPIC_API_KEY"
        fi
    else
        prompt_key "  (*) Anthropic API key: " "ANTHROPIC_API_KEY" "true"
    fi

    echo ""

    # Optional keys
    info "The following keys are optional but enable additional data sources."
    echo ""

    prompt_key "  NewsAPI key (optional, press Enter to skip): " "NEWSAPI_KEY"
    prompt_key "  Finnhub API key (optional, press Enter to skip): " "FINNHUB_API_KEY"

    echo ""
    chmod 600 "$KLEITOS_ENV"
    success "Environment file secured (chmod 600)"
}

# ---------------------------------------------------------------------------
# OpenClaw onboarding
# ---------------------------------------------------------------------------
run_openclaw_onboard() {
    header "OpenClaw Onboarding"

    if command -v openclaw &>/dev/null; then
        info "Running OpenClaw onboarding..."
        openclaw onboard || {
            warn "OpenClaw onboarding returned a non-zero exit code."
            warn "You can run 'openclaw onboard' manually later."
        }
        success "OpenClaw onboarding complete"
    else
        warn "OpenClaw not found in PATH. Skipping onboarding."
        warn "Run 'npm install -g openclaw' and then 'openclaw onboard'."
    fi
}

# ---------------------------------------------------------------------------
# Database initialization
# ---------------------------------------------------------------------------
init_database() {
    header "Database Initialization"

    local db_path="$KLEITOS_DATA/db/kleitos.db"

    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate" 2>/dev/null || {
        warn "Could not activate venv. Skipping database init."
        return
    }

    if [[ -f "$db_path" ]]; then
        info "Database already exists at $db_path"
        local table_count
        table_count="$(sqlite3 "$db_path" "SELECT count(*) FROM sqlite_master WHERE type='table';" 2>/dev/null || echo "0")"
        info "Tables found: $table_count"

        if [[ "$table_count" -gt 0 ]]; then
            success "Database appears to be initialized"
            return
        fi
    fi

    info "Initializing database..."
    if [[ -f "$KLEITOS_HOME/scripts/init_db.py" ]]; then
        python "$KLEITOS_HOME/scripts/init_db.py"
        success "Database initialized"
    elif python -c "from kleitos.db import init_db; init_db()" 2>/dev/null; then
        success "Database initialized via module"
    else
        warn "No database init script found. You may need to initialize manually."
    fi
}

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
quick_health_check() {
    header "Quick Health Check"

    # Check env file
    if [[ -f "$KLEITOS_ENV" ]]; then
        local key_count
        key_count="$(grep -c '=' "$KLEITOS_ENV" 2>/dev/null || echo "0")"
        success "Environment file: $key_count keys configured"
    else
        error "Environment file not found at $KLEITOS_ENV"
    fi

    # Check venv
    if [[ -d "$VENV_DIR" ]]; then
        success "Virtual environment: present"
    else
        error "Virtual environment: missing"
    fi

    # Check data dirs
    local data_ok=true
    for dir in db logs backups exports; do
        if [[ ! -d "$KLEITOS_DATA/$dir" ]]; then
            error "Data directory missing: $KLEITOS_DATA/$dir"
            data_ok=false
        fi
    done
    if $data_ok; then
        success "Data directories: all present"
    fi

    # Check database
    local db_path="$KLEITOS_DATA/db/kleitos.db"
    if [[ -f "$db_path" ]]; then
        local db_size
        db_size="$(stat -f%z "$db_path" 2>/dev/null || echo "unknown")"
        success "Database: present (${db_size} bytes)"
    else
        warn "Database: not found"
    fi

    # Check Anthropic key
    local anthropic_key
    anthropic_key="$(grep '^ANTHROPIC_API_KEY=' "$KLEITOS_ENV" 2>/dev/null | cut -d= -f2- || true)"
    if [[ -n "$anthropic_key" ]]; then
        success "Anthropic API key: configured"
    else
        error "Anthropic API key: NOT configured"
    fi
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print_summary() {
    header "Configuration Summary"

    echo "  Configuration file: $KLEITOS_ENV"
    echo ""

    printf "  %-25s %s\n" "Setting" "Status"
    printf "  %-25s %s\n" "-------------------------" "----------"

    # Read keys from env
    local anthropic newsapi finnhub
    anthropic="$(grep '^ANTHROPIC_API_KEY=' "$KLEITOS_ENV" 2>/dev/null | cut -d= -f2- || true)"
    newsapi="$(grep '^NEWSAPI_KEY=' "$KLEITOS_ENV" 2>/dev/null | cut -d= -f2- || true)"
    finnhub="$(grep '^FINNHUB_API_KEY=' "$KLEITOS_ENV" 2>/dev/null | cut -d= -f2- || true)"

    if [[ -n "$anthropic" ]]; then
        printf "  %-25s ${GREEN}Configured${NC}\n" "Anthropic API Key"
    else
        printf "  %-25s ${RED}Missing${NC}\n" "Anthropic API Key"
    fi

    if [[ -n "$newsapi" ]]; then
        printf "  %-25s ${GREEN}Configured${NC}\n" "NewsAPI Key"
    else
        printf "  %-25s ${YELLOW}Not set${NC}\n" "NewsAPI Key"
    fi

    if [[ -n "$finnhub" ]]; then
        printf "  %-25s ${GREEN}Configured${NC}\n" "Finnhub API Key"
    else
        printf "  %-25s ${YELLOW}Not set${NC}\n" "Finnhub API Key"
    fi

    echo ""
    printf "  ${BOLD}To start Axion, run:${NC}\n"
    printf "    ${BLUE}./start.sh${NC}\n\n"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    printf "\n${BOLD}${BLUE}  Axion by 4Labs - Configuration Wizard${NC}\n\n"

    if [[ ! -f "$KLEITOS_ENV" ]]; then
        warn "Environment file not found. Run install.sh first, or a default will be created."
        touch "$KLEITOS_ENV"
        chmod 600 "$KLEITOS_ENV"
    fi

    configure_api_keys
    run_openclaw_onboard
    init_database
    quick_health_check
    print_summary
}

main "$@"
