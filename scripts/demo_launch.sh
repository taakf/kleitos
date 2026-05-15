#!/usr/bin/env bash
# Axion Demo Launcher — one command to prepare and serve
# Usage: bash scripts/demo_launch.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PORT=7777
URL="http://localhost:$PORT/dashboard"

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║       Axion Demo Launcher            ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# 1. Kill any stale server on the port
if lsof -ti:$PORT >/dev/null 2>&1; then
    echo "  → Stopping existing server on port $PORT..."
    lsof -ti:$PORT | xargs kill -9 2>/dev/null || true
    sleep 1
fi

# 2. Run demo-prep (reset + populate)
echo "  → Preparing demo data..."
if ! .venv/bin/python scripts/demo_prep.py --reset 2>&1 | tail -3; then
    echo ""
    echo "  ✗ Demo prep failed. Check the output above."
    exit 1
fi
echo ""

# 3. Start the server in background
echo "  → Starting Axion server..."
.venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port $PORT --log-level warning &
SERVER_PID=$!

# 4. Wait for health check (up to 15 seconds)
echo -n "  → Waiting for server"
for i in $(seq 1 30); do
    if curl -sf "http://localhost:$PORT/api/v1/health" >/dev/null 2>&1; then
        echo " ready!"
        echo ""
        echo "  ╔══════════════════════════════════════╗"
        echo "  ║  ✓ Axion is running                  ║"
        echo "  ║                                      ║"
        echo "  ║  Open: $URL  ║"
        echo "  ║                                      ║"
        echo "  ║  Stop: kill $SERVER_PID or Ctrl+C          ║"
        echo "  ╚══════════════════════════════════════╝"
        echo ""
        # Keep script alive so Ctrl+C kills the server
        wait $SERVER_PID
        exit 0
    fi
    echo -n "."
    sleep 0.5
done

# Health check failed
echo " failed!"
echo ""
echo "  ✗ Server did not become healthy within 15 seconds."
echo "  Check logs: .venv/bin/python -m uvicorn src.main:app --port $PORT"
kill $SERVER_PID 2>/dev/null || true
exit 1
