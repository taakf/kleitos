#!/usr/bin/env bash
# =============================================================================
# Axion by 4Labs — Local Launcher (macOS / Linux)
#
# One-command "double-click and go" path for end users.
#
#   1. Verifies Python 3.11+
#   2. Creates .venv if missing
#   3. Installs requirements.txt
#   4. Prepares the data directory + rotates logs
#   5. Runs migrations (scripts/migrate.py owns customer-facing messages)
#   6. Checks port 7777 (or AXION_PORT) and shows PID/process on conflict
#   7. Starts uvicorn on 127.0.0.1:${PORT} (log mirrored to axion-server.log)
#   8. Opens the dashboard
#
# Exits cleanly on port conflicts, missing Python, dependency failures, etc.
# No Docker, no Homebrew, no launchd. Pure local.
# =============================================================================

set -uo pipefail

# ── Colors ───────────────────────────────────────────────────────────────────
if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
    BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
    BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'
else
    BLUE=''; GREEN=''; YELLOW=''; RED=''; BOLD=''; DIM=''; NC=''
fi

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
export KLEITOS_DATA_DIR="$DATA_DIR"
export KLEITOS_DB_PATH="$DATA_DIR/db/kleitos.db"

LOG_DIR="$DATA_DIR/logs"
LAUNCHER_LOG="$LOG_DIR/axion-launcher.log"
SERVER_LOG="$LOG_DIR/axion-server.log"
MIGRATE_LOG="$LOG_DIR/axion-migration.log"
mkdir -p "$LOG_DIR"

# ── Output helpers — write to console AND axion-launcher.log ─────────────────
_log_to_file() {
    # Strip ANSI colour codes before writing to the file.
    printf '%s\n' "$*" | sed $'s/\033\\[[0-9;]*m//g' >> "$LAUNCHER_LOG"
}

stage()   { printf "${BLUE}[%s]${NC}    %s\n" "$1" "$2"; _log_to_file "[$1] $2"; }
info()    { printf "${BLUE}[INFO]${NC}  %s\n"  "$*";    _log_to_file "[INFO]  $*"; }
ok()      { printf "${GREEN}[OK]${NC}    %s\n" "$*";    _log_to_file "[OK]    $*"; }
warn()    { printf "${YELLOW}[WARN]${NC}  %s\n" "$*";   _log_to_file "[WARN]  $*"; }
fail()    { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2;  _log_to_file "[ERROR] $*"; }

failure_hint() {
    printf "\n${DIM}  For diagnostics:${NC}\n"
    printf "${DIM}    %s\n" "    ${VENV_DIR}/bin/python scripts/support_bundle.py"
    printf "${DIM}    %s\n" "    (creates a redacted zip at ${DATA_DIR}/support/)"
    printf "${DIM}    %s\n${NC}" "  Launcher log: ${LAUNCHER_LOG}"
}

# ── Banner + run header ──────────────────────────────────────────────────────
printf "\n${BOLD}${BLUE}  Axion by 4Labs — Local Launcher${NC}\n\n"
info "Project root : $PROJECT_ROOT"
info "Data dir     : $DATA_DIR"
info "Logs         : $LOG_DIR"
info "Port         : $PORT"
echo ""

# Header in the launcher log so each run is easy to find later.
{
    printf '\n=== launcher run %s ===\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf '  PROJECT_ROOT=%s\n' "$PROJECT_ROOT"
    printf '  DATA_DIR=%s\n'     "$DATA_DIR"
    printf '  PORT=%s\n'         "$PORT"
} >> "$LAUNCHER_LOG"

# Rotate any log file that has crept past the cap before we start writing more.
if command -v "$VENV_DIR/bin/python" >/dev/null 2>&1; then
    "$VENV_DIR/bin/python" "$PROJECT_ROOT/scripts/rotate_logs.py" "$LOG_DIR" >/dev/null 2>&1 || true
fi

# ── 1/7. Find Python 3.11+ ───────────────────────────────────────────────────
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
    failure_hint
    exit 1
fi
stage "1/7" "Python    : $PYTHON ($($PYTHON --version 2>&1))"

# ── 2/7. Virtual environment ─────────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
    info "First run detected — setting up a fresh virtual environment ..."
    "$PYTHON" -m venv "$VENV_DIR"
    stage "2/7" "Virtual env: created at .venv"
else
    stage "2/7" "Virtual env: reusing existing .venv"
fi

VENV_PY="$VENV_DIR/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
    fail "Virtual environment is broken (missing $VENV_PY). Delete .venv and retry."
    failure_hint
    exit 1
fi

# ── 3/7. Dependencies ────────────────────────────────────────────────────────
MARKER="$VENV_DIR/.deps-installed"
if [[ ! -f "$MARKER" ]] || [[ "$PROJECT_ROOT/requirements.txt" -nt "$MARKER" ]]; then
    info "Installing dependencies (first run can take 1–2 minutes) ..."
    "$VENV_PY" -m pip install --upgrade pip --quiet
    if ! "$VENV_PY" -m pip install -r "$PROJECT_ROOT/requirements.txt" --quiet; then
        fail "pip install failed. Check your internet connection and requirements.txt."
        failure_hint
        exit 1
    fi
    touch "$MARKER"
    stage "3/7" "Deps      : installed"
else
    stage "3/7" "Deps      : up to date"
fi

# ── 4/7. Data directory + log rotation ──────────────────────────────────────
mkdir -p "$DATA_DIR/db" "$DATA_DIR/logs" "$DATA_DIR/backups" "$DATA_DIR/exports" "$DATA_DIR/support"
"$VENV_PY" "$PROJECT_ROOT/scripts/rotate_logs.py" "$LOG_DIR" >/dev/null 2>&1 || true
stage "4/7" "Data dir  : $DATA_DIR"

# ── 5/7. Migrations (clean output via scripts/migrate.py) ───────────────────
info "Running migrations ..."
# Tee migration output to a dedicated log file so support bundles see it.
"$VENV_PY" "$PROJECT_ROOT/scripts/migrate.py" 2>&1 | tee -a "$MIGRATE_LOG"
MIGRATE_RC="${PIPESTATUS[0]}"
case "$MIGRATE_RC" in
    0)
        stage "5/7" "Database  : at schema head"
        ;;
    2)
        echo ""
        fail "Cannot start: database is newer than this version of Axion."
        echo "    See the message above for recovery steps."
        failure_hint
        exit 2
        ;;
    3)
        echo ""
        fail "Cannot start: database is corrupt or unreadable."
        echo "    See the message above for recovery steps."
        failure_hint
        exit 3
        ;;
    4)
        echo ""
        fail "Cannot start: pre-migration backup failed."
        echo "    See the message above for recovery steps."
        failure_hint
        exit 4
        ;;
    *)
        echo ""
        fail "Migrations failed (see above)."
        failure_hint
        exit 1
        ;;
esac

# ── 6/7. Port conflict check ────────────────────────────────────────────────
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

# Best-effort: identify the listening process so the customer knows who to blame.
port_owner_human() {
    if command -v lsof >/dev/null 2>&1; then
        # Output: "<name> <pid>"
        lsof -nP -i ":$1" -sTCP:LISTEN -F pcn 2>/dev/null | awk '
            /^p/ { pid=substr($0,2) }
            /^c/ { cmd=substr($0,2) }
            /^n/ { if (pid && cmd) { print cmd " (pid " pid ")"; exit } }
        '
    fi
}

if port_in_use "$PORT"; then
    if curl -sf "http://$HOST:$PORT/api/v1/health" >/dev/null 2>&1; then
        ok "Axion is already running at http://$HOST:$PORT"
        if [[ "$(uname -s)" == "Darwin" ]]; then
            open "http://$HOST:$PORT/dashboard/" 2>/dev/null || true
        fi
        exit 0
    fi
    OWNER="$(port_owner_human "$PORT" || true)"
    fail "Port $PORT is in use by another application."
    if [[ -n "${OWNER:-}" ]]; then
        echo "  Owner   : $OWNER"
    fi
    echo ""
    echo "  Options:"
    echo "    1. Close the other application."
    echo "    2. Or run Axion on a different port:"
    echo "         AXION_PORT=7778 ./scripts/run_local.sh"
    echo ""
    failure_hint
    exit 2
fi
stage "6/7" "Port      : $PORT free"

# ── 7/7. Start uvicorn ─────────────────────────────────────────────────────
echo ""
info "Starting Axion on http://$HOST:$PORT ..."
echo ""

# Wait-for-health watchdog opens the dashboard once the server is responsive.
(
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
            printf "    Support   : ${VENV_PY} scripts/support_bundle.py\n"
            printf "    Stop      : Ctrl+C\n\n"
            if [[ "$(uname -s)" == "Darwin" ]]; then
                open "http://$HOST:$PORT/dashboard/" 2>/dev/null || true
            fi
            break
        fi
    done
) &

# Mirror server output to axion-server.log while still printing to the console.
"$VENV_PY" -m uvicorn src.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --log-level info 2>&1 | tee -a "$SERVER_LOG"
exit "${PIPESTATUS[0]}"
