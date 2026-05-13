#!/usr/bin/env bash
# =============================================================================
# Axion by 4Labs — Local Launcher (macOS / Linux)
#
# One-command "double-click and go" path for end users.
#
#   1. Verifies Python 3.11+
#   2. Creates .venv if missing
#   3. Installs requirements.txt
#   4. Creates the data directory (~/axion-data, or honours AXION_DATA_DIR)
#   5. Runs migrations on the SQLite database
#   6. Starts uvicorn on 127.0.0.1:${AXION_PORT:-7777}
#   7. Opens the dashboard in the default browser (macOS only — auto)
#
# Exits cleanly on port conflicts, missing Python, dependency failures, etc.
# No Docker, no Homebrew, no launchd. Pure local.
# =============================================================================

set -euo pipefail

# ── Colors ───────────────────────────────────────────────────────────────────
if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
    BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
    BOLD='\033[1m'; NC='\033[0m'
else
    BLUE=''; GREEN=''; YELLOW=''; RED=''; BOLD=''; NC=''
fi

info()    { printf "${BLUE}[INFO]${NC}  %s\n" "$*"; }
ok()      { printf "${GREEN}[OK]${NC}    %s\n" "$*"; }
warn()    { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
fail()    { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; }

# ── Resolve project root (this script lives in scripts/) ─────────────────────
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$PROJECT_ROOT"

# ── Configuration ────────────────────────────────────────────────────────────
PORT="${AXION_PORT:-${KLEITOS_PORT:-7777}}"
HOST="127.0.0.1"
VENV_DIR="$PROJECT_ROOT/.venv"

# Data dir: prefer existing ~/kleitos-data for back-compat, else ~/axion-data
if [[ -n "${AXION_DATA_DIR:-}" ]]; then
    DATA_DIR="$AXION_DATA_DIR"
elif [[ -n "${KLEITOS_DATA_DIR:-}" ]]; then
    DATA_DIR="$KLEITOS_DATA_DIR"
elif [[ -d "$HOME/kleitos-data" ]] && [[ ! -d "$HOME/axion-data" ]]; then
    DATA_DIR="$HOME/kleitos-data"
else
    DATA_DIR="$HOME/axion-data"
fi

export AXION_DATA_DIR="$DATA_DIR"
export AXION_DB_PATH="$DATA_DIR/db/kleitos.db"
# Back-compat: some code still reads KLEITOS_* names
export KLEITOS_DATA_DIR="$DATA_DIR"
export KLEITOS_DB_PATH="$DATA_DIR/db/kleitos.db"

DB_PATH="$DATA_DIR/db/kleitos.db"
LOG_DIR="$DATA_DIR/logs"
PID_FILE="$DATA_DIR/axion.pid"

printf "\n${BOLD}${BLUE}  Axion by 4Labs — Local Launcher${NC}\n\n"
info "Project root : $PROJECT_ROOT"
info "Data dir     : $DATA_DIR"
info "Port         : $PORT"
echo ""

# ── 1. Find Python 3.11+ ─────────────────────────────────────────────────────
PYTHON=""
for cand in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        ver=$("$cand" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
        major=${ver%%.*}
        minor=${ver##*.}
        if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
            PYTHON="$cand"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    fail "Python 3.11 or newer is required but was not found on this system."
    echo ""
    echo "  Install options:"
    echo "    macOS:   brew install python@3.12   (or download from python.org)"
    echo "    Linux:   sudo apt install python3.12  (or your distro's package)"
    echo ""
    exit 1
fi
ok "Python ($PYTHON → $($PYTHON --version 2>&1))"

# ── 2. Virtual environment ───────────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment at .venv ..."
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Virtual environment created"
else
    ok "Virtual environment exists"
fi

VENV_PY="$VENV_DIR/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
    fail "Virtual environment is broken (missing $VENV_PY). Delete .venv and retry."
    exit 1
fi

# ── 3. Dependencies ──────────────────────────────────────────────────────────
# Use a marker file to skip pip install on subsequent launches.
MARKER="$VENV_DIR/.deps-installed"
if [[ ! -f "$MARKER" ]] || [[ "$PROJECT_ROOT/requirements.txt" -nt "$MARKER" ]]; then
    info "Installing dependencies (this can take 1–2 minutes on first run) ..."
    "$VENV_PY" -m pip install --upgrade pip --quiet
    if ! "$VENV_PY" -m pip install -r "$PROJECT_ROOT/requirements.txt" --quiet; then
        fail "pip install failed. Check your internet connection and requirements.txt."
        exit 1
    fi
    touch "$MARKER"
    ok "Dependencies installed"
else
    ok "Dependencies up to date"
fi

# ── 4. Data directory ────────────────────────────────────────────────────────
mkdir -p "$DATA_DIR/db" "$DATA_DIR/logs" "$DATA_DIR/backups" "$DATA_DIR/exports"
ok "Data dir ready ($DATA_DIR)"

# ── 5. Run migrations ────────────────────────────────────────────────────────
# scripts/migrate.py prints clean, customer-facing output and returns a
# structured exit code so this launcher doesn't have to format messages
# itself. Exit codes are documented at the top of that script.
info "Running migrations ..."
"$VENV_PY" "$PROJECT_ROOT/scripts/migrate.py"
MIGRATE_RC=$?
case "$MIGRATE_RC" in
    0)
        ok "Database is at schema head"
        ;;
    2)
        echo ""
        fail "Cannot start: database is newer than this version of Axion."
        echo "    See the message above for recovery steps."
        exit 2
        ;;
    3)
        echo ""
        fail "Cannot start: database is corrupt or unreadable."
        echo "    See the message above for recovery steps."
        exit 3
        ;;
    4)
        echo ""
        fail "Cannot start: pre-migration backup failed."
        echo "    See the message above for recovery steps."
        exit 4
        ;;
    *)
        echo ""
        fail "Migrations failed (see above)."
        exit 1
        ;;
esac

# ── 6. Check port ────────────────────────────────────────────────────────────
port_in_use() {
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -i ":$1" -sTCP:LISTEN >/dev/null 2>&1
    elif command -v ss >/dev/null 2>&1; then
        ss -ltn "sport = :$1" 2>/dev/null | grep -q ":$1"
    elif command -v netstat >/dev/null 2>&1; then
        netstat -an 2>/dev/null | grep -q "\.${1} .*LISTEN"
    else
        return 1
    fi
}

# If Axion is already running on this port, just open the dashboard.
if port_in_use "$PORT"; then
    if curl -sf "http://$HOST:$PORT/api/v1/health" >/dev/null 2>&1; then
        ok "Axion is already running at http://$HOST:$PORT"
        if [[ "$(uname -s)" == "Darwin" ]]; then
            open "http://$HOST:$PORT/dashboard/" 2>/dev/null || true
        fi
        exit 0
    fi
    fail "Port $PORT is in use by another application."
    echo ""
    echo "  Close the other application, or run with a different port:"
    echo "    AXION_PORT=7778 ./scripts/run_local.sh"
    echo ""
    exit 2
fi

# ── 7. Start uvicorn ─────────────────────────────────────────────────────────
echo ""
info "Starting Axion on http://$HOST:$PORT ..."
echo ""

# Run uvicorn in foreground so Ctrl+C stops it cleanly.
# Open the dashboard once the server is healthy, then hand the terminal to uvicorn.
(
    # Wait up to 30s for health, then open browser (macOS only).
    for _ in $(seq 1 30); do
        sleep 1
        if curl -sf "http://$HOST:$PORT/api/v1/health" >/dev/null 2>&1; then
            printf "\n${GREEN}  ============================================${NC}\n"
            printf "${GREEN}    Axion is running.${NC}\n"
            printf "${GREEN}  ============================================${NC}\n"
            printf "    Dashboard : ${BOLD}http://$HOST:$PORT${NC}\n"
            printf "    API docs  : http://$HOST:$PORT/docs\n"
            printf "    Data      : %s\n" "$DATA_DIR"
            printf "    Logs      : %s\n" "$LOG_DIR"
            printf "    Stop      : Ctrl+C\n\n"
            if [[ "$(uname -s)" == "Darwin" ]]; then
                open "http://$HOST:$PORT/dashboard/" 2>/dev/null || true
            fi
            break
        fi
    done
) &

exec "$VENV_PY" -m uvicorn src.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --log-level info
