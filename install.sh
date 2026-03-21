#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Axion by 4Labs - One-Command Bootstrap Installer
# Installs all dependencies and configures the system on macOS (Apple Silicon)
# =============================================================================

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info()    { printf "${BLUE}[INFO]${NC}  %s\n" "$*"; }
success() { printf "${GREEN}[OK]${NC}    %s\n" "$*"; }
warn()    { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
error()   { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; }
header()  { printf "\n${BOLD}=== %s ===${NC}\n\n" "$*"; }

# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        error "Installation failed at line $BASH_LINENO with exit code $exit_code."
        error "Please check the output above for details."
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
KLEITOS_HOME="$HOME/kleitos"
KLEITOS_DATA="$HOME/kleitos-data"
KLEITOS_ENV="$HOME/.kleitos.env"
VENV_DIR="$KLEITOS_HOME/venv"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"

PLIST_CORE="com.kleitos.core.plist"
PLIST_OPENCLAW="com.kleitos.openclaw.plist"

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
preflight() {
    header "Pre-flight Checks"

    # Must be macOS
    if [[ "$(uname -s)" != "Darwin" ]]; then
        error "This installer only supports macOS. Detected: $(uname -s)"
        exit 1
    fi
    success "Running on macOS ($(sw_vers -productVersion))"

    # Architecture info
    local arch
    arch="$(uname -m)"
    info "Architecture: $arch"
    if [[ "$arch" == "arm64" ]]; then
        success "Apple Silicon detected"
    else
        warn "Expected Apple Silicon (arm64), detected $arch. Proceeding anyway."
    fi
}

# ---------------------------------------------------------------------------
# Homebrew
# ---------------------------------------------------------------------------
install_homebrew() {
    header "Homebrew"

    if command -v brew &>/dev/null; then
        success "Homebrew already installed ($(brew --version | head -1))"
    else
        info "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

        # Add brew to PATH for Apple Silicon default location
        if [[ -f /opt/homebrew/bin/brew ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi
        success "Homebrew installed"
    fi
}

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------
install_python() {
    header "Python"

    if command -v python3 &>/dev/null; then
        local py_version
        py_version="$(python3 --version 2>&1 | awk '{print $2}')"
        local py_major py_minor
        py_major="$(echo "$py_version" | cut -d. -f1)"
        py_minor="$(echo "$py_version" | cut -d. -f2)"

        if [[ "$py_major" -ge 3 && "$py_minor" -ge 11 ]]; then
            success "Python $py_version already installed"
            return
        else
            warn "Python $py_version found but 3.11+ required"
        fi
    fi

    info "Installing Python 3.11+ via Homebrew..."
    brew install python@3.12
    success "Python installed ($(python3 --version))"
}

# ---------------------------------------------------------------------------
# Node.js
# ---------------------------------------------------------------------------
install_node() {
    header "Node.js"

    if command -v node &>/dev/null; then
        local node_version
        node_version="$(node --version | sed 's/v//')"
        local node_major
        node_major="$(echo "$node_version" | cut -d. -f1)"

        if [[ "$node_major" -ge 18 ]]; then
            success "Node.js v$node_version already installed"
            return
        else
            warn "Node.js v$node_version found but 18+ required"
        fi
    fi

    info "Installing Node.js 18+ via Homebrew..."
    brew install node@20
    success "Node.js installed ($(node --version))"
}

# ---------------------------------------------------------------------------
# Python virtual environment
# ---------------------------------------------------------------------------
setup_venv() {
    header "Python Virtual Environment"

    if [[ -d "$VENV_DIR" ]]; then
        success "Virtual environment already exists at $VENV_DIR"
    else
        info "Creating virtual environment at $VENV_DIR..."
        python3 -m venv "$VENV_DIR"
        success "Virtual environment created"
    fi

    info "Activating virtual environment..."
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"

    info "Upgrading pip..."
    pip install --upgrade pip --quiet

    if [[ -f "$KLEITOS_HOME/requirements.txt" ]]; then
        info "Installing Python dependencies from requirements.txt..."
        pip install -r "$KLEITOS_HOME/requirements.txt" --quiet
        success "Python dependencies installed"
    else
        warn "requirements.txt not found at $KLEITOS_HOME/requirements.txt - skipping"
    fi
}

# ---------------------------------------------------------------------------
# OpenClaw
# ---------------------------------------------------------------------------
install_openclaw() {
    header "OpenClaw"

    if command -v openclaw &>/dev/null; then
        success "OpenClaw already installed"
    else
        info "Installing OpenClaw globally via npm..."
        npm install -g openclaw
        success "OpenClaw installed"
    fi
}

# ---------------------------------------------------------------------------
# Data directories
# ---------------------------------------------------------------------------
create_data_dirs() {
    header "Data Directories"

    local dirs=("db" "logs" "backups" "exports")

    for dir in "${dirs[@]}"; do
        local full_path="$KLEITOS_DATA/$dir"
        if [[ -d "$full_path" ]]; then
            success "Directory exists: $full_path"
        else
            mkdir -p "$full_path"
            success "Created: $full_path"
        fi
    done
}

# ---------------------------------------------------------------------------
# Environment file
# ---------------------------------------------------------------------------
setup_env() {
    header "Environment Configuration"

    if [[ -f "$KLEITOS_ENV" ]]; then
        success "Environment file already exists at $KLEITOS_ENV"
    else
        if [[ -f "$KLEITOS_HOME/.env.template" ]]; then
            info "Creating $KLEITOS_ENV from .env.template..."
            cp "$KLEITOS_HOME/.env.template" "$KLEITOS_ENV"
            success "Environment file created"
        else
            info "Creating default $KLEITOS_ENV..."
            cat > "$KLEITOS_ENV" << 'ENVEOF'
# Axion by 4Labs - Environment Configuration
# -----------------------------------
# Required
ANTHROPIC_API_KEY=

# Optional API Keys
NEWSAPI_KEY=
FINNHUB_API_KEY=

# Application Settings
KLEITOS_HOST=0.0.0.0
KLEITOS_PORT=7777
KLEITOS_DATA_DIR=$HOME/kleitos-data
KLEITOS_DB_PATH=$HOME/kleitos-data/db/kleitos.db
KLEITOS_LOG_DIR=$HOME/kleitos-data/logs

# OpenClaw Settings
OPENCLAW_PORT=3000
ENVEOF
            success "Default environment file created"
        fi
    fi

    info "Setting file permissions on $KLEITOS_ENV..."
    chmod 600 "$KLEITOS_ENV"
    success "Permissions set (600) on $KLEITOS_ENV"
}

# ---------------------------------------------------------------------------
# Database initialization
# ---------------------------------------------------------------------------
init_database() {
    header "Database Initialization"

    local db_path="$KLEITOS_DATA/db/kleitos.db"

    if [[ -f "$db_path" ]]; then
        success "Database already exists at $db_path"
        return
    fi

    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"

    info "Initializing SQLite database..."
    if [[ -f "$KLEITOS_HOME/scripts/init_db.py" ]]; then
        python "$KLEITOS_HOME/scripts/init_db.py"
        success "Database initialized via init_db.py"
    elif python -c "from kleitos.db import init_db; init_db()" 2>/dev/null; then
        success "Database initialized via kleitos.db module"
    else
        # Create a minimal database so the file exists
        sqlite3 "$db_path" "SELECT 1;" >/dev/null 2>&1
        success "Database file created at $db_path"
        warn "Could not find init script - database may need manual initialization"
    fi
}

# ---------------------------------------------------------------------------
# Launchd plists
# ---------------------------------------------------------------------------
install_launchd() {
    header "Launchd Configuration"

    mkdir -p "$LAUNCH_AGENTS"

    # --- Axion Core plist ---
    local plist_core_path="$LAUNCH_AGENTS/$PLIST_CORE"
    info "Installing $PLIST_CORE..."
    cat > "$plist_core_path" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.kleitos.core</string>

    <key>ProgramArguments</key>
    <array>
        <string>${VENV_DIR}/bin/python</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>src.main:app</string>
        <string>--host</string>
        <string>0.0.0.0</string>
        <string>--port</string>
        <string>7777</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${KLEITOS_HOME}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${VENV_DIR}/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>KLEITOS_ENV_FILE</key>
        <string>${KLEITOS_ENV}</string>
    </dict>

    <key>StandardOutPath</key>
    <string>${KLEITOS_DATA}/logs/kleitos-core.log</string>
    <key>StandardErrorPath</key>
    <string>${KLEITOS_DATA}/logs/kleitos-core-error.log</string>

    <key>RunAtLoad</key>
    <false/>

    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
PLISTEOF
    success "Installed $plist_core_path"

    # --- OpenClaw Gateway plist ---
    local plist_oc_path="$LAUNCH_AGENTS/$PLIST_OPENCLAW"
    info "Installing $PLIST_OPENCLAW..."
    cat > "$plist_oc_path" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.kleitos.openclaw</string>

    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/openclaw</string>
        <string>serve</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${KLEITOS_HOME}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>KLEITOS_ENV_FILE</key>
        <string>${KLEITOS_ENV}</string>
    </dict>

    <key>StandardOutPath</key>
    <string>${KLEITOS_DATA}/logs/openclaw.log</string>
    <key>StandardErrorPath</key>
    <string>${KLEITOS_DATA}/logs/openclaw-error.log</string>

    <key>RunAtLoad</key>
    <false/>

    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
PLISTEOF
    success "Installed $plist_oc_path"
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print_summary() {
    header "Installation Complete"

    printf "${GREEN}${BOLD}"
    echo "  Axion by 4Labs has been installed successfully!"
    printf "${NC}\n"

    echo "  Installed components:"
    echo "    - Python $(python3 --version 2>&1 | awk '{print $2}')"
    echo "    - Node.js $(node --version 2>/dev/null || echo 'N/A')"
    echo "    - Virtual environment at $VENV_DIR"
    echo "    - Data directory at $KLEITOS_DATA"
    echo "    - Environment config at $KLEITOS_ENV"
    echo "    - Launchd plists in $LAUNCH_AGENTS"
    echo ""
    printf "${BOLD}  Next steps:${NC}\n"
    echo "    1. Run the configuration wizard:"
    printf "       ${BLUE}./setup.sh${NC}\n"
    echo ""
    echo "    2. Start the services:"
    printf "       ${BLUE}./start.sh${NC}\n"
    echo ""
    echo "    3. Check status:"
    printf "       ${BLUE}./status.sh${NC}\n"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    printf "\n${BOLD}${BLUE}"
    echo "     _          _              "
    echo "    / \   __  _(_) ___  _ __  "
    echo "   / _ \  \ \/ / |/ _ \| '_ \ "
    echo "  / ___ \  >  <| | (_) | | | |"
    echo " /_/   \_\/_/\_\_|\___/|_| |_|"
    echo "                               "
    printf "${NC}\n"
    echo "  Axion by 4Labs - One-Command Bootstrap Installer"
    echo ""

    preflight
    install_homebrew
    install_python
    install_node
    setup_venv
    install_openclaw
    create_data_dirs
    setup_env
    init_database
    install_launchd
    print_summary
}

main "$@"
