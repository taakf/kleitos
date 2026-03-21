#!/bin/bash
# Axion by 4Labs — Mac Mini Deployment Script
# Deploys Axion + OpenClaw for 24/7 autonomous operation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
# NOTE: This Docker-based deployment uses the same plist name as the native Axion install.
# If both are configured, the native install takes precedence.
PLIST_NAME="com.kleitos.app"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
KLEITOS_PORT="${KLEITOS_PORT:-7777}"
KLEITOS_DATA="${KLEITOS_DATA:-$HOME/kleitos-data}"
LOG_DIR="$KLEITOS_DATA/logs"

echo "================================================="
echo "  Axion by 4Labs — Mac Mini Deployment"
echo "================================================="

# 1. Check prerequisites
echo ""
echo "--- Checking prerequisites ---"
command -v docker >/dev/null 2>&1 || { echo "ERROR: Docker is required. Install Docker Desktop for Mac."; exit 1; }
command -v docker compose >/dev/null 2>&1 || { echo "ERROR: Docker Compose is required."; exit 1; }
echo "  Docker: $(docker --version)"
echo "  Docker Compose: $(docker compose version)"

# 2. Create data directories
echo ""
echo "--- Setting up data directories ---"
mkdir -p "$KLEITOS_DATA/db" "$KLEITOS_DATA/logs" "$KLEITOS_DATA/backups"
echo "  Data directory: $KLEITOS_DATA"

# 3. Check environment
echo ""
echo "--- Checking environment ---"
ENV_FILE="$HOME/.kleitos.env"
if [ -f "$ENV_FILE" ]; then
    echo "  Environment file found: $ENV_FILE"
else
    echo "  WARNING: No $ENV_FILE found."
    echo "  Create it with at minimum:"
    echo "    ANTHROPIC_API_KEY=sk-ant-..."
    echo ""
    read -p "  Continue without env file? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then exit 1; fi
fi

# 4. Build and start Docker container
echo ""
echo "--- Building Docker image ---"
cd "$PROJECT_DIR"
docker compose build --no-cache

echo ""
echo "--- Starting Axion ---"
docker compose up -d

# 5. Wait for health check
echo ""
echo "--- Waiting for health check ---"
for i in $(seq 1 30); do
    if curl -sf "http://localhost:${KLEITOS_PORT}/api/v1/health" > /dev/null 2>&1; then
        echo "  Axion is healthy!"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "  ERROR: Health check failed after 30 seconds."
        echo "  Check logs: docker compose logs axion"
        exit 1
    fi
    sleep 1
done

# 6. Create launchd plist for auto-start
echo ""
echo "--- Setting up auto-start (launchd) ---"
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/docker</string>
        <string>compose</string>
        <string>-f</string>
        <string>${PROJECT_DIR}/docker-compose.yml</string>
        <string>up</string>
        <string>-d</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/launchd-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/launchd-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
PLIST

# Clean up old plist name if it exists
OLD_PLIST="$HOME/Library/LaunchAgents/com.kleitos.api.plist"
if [ -f "$OLD_PLIST" ]; then
    launchctl unload "$OLD_PLIST" 2>/dev/null || true
    rm -f "$OLD_PLIST"
fi

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"
echo "  launchd plist installed: $PLIST_PATH"
echo "  Axion will auto-start on login."

# 7. Set up OpenClaw config
echo ""
echo "--- Configuring OpenClaw ---"
OPENCLAW_DIR="$HOME/.openclaw"
mkdir -p "$OPENCLAW_DIR"

# Copy workspace files
if [ -d "$PROJECT_DIR/openclaw/workspaces" ]; then
    cp -r "$PROJECT_DIR/openclaw/workspaces" "$OPENCLAW_DIR/"
    echo "  Workspaces copied to $OPENCLAW_DIR/workspaces"
fi

# Copy config
if [ -f "$PROJECT_DIR/openclaw/openclaw-config.json" ]; then
    cp "$PROJECT_DIR/openclaw/openclaw-config.json" "$OPENCLAW_DIR/openclaw.json"
    echo "  Config copied to $OPENCLAW_DIR/openclaw.json"
fi

# 8. Summary
echo ""
echo "================================================="
echo "  Deployment Complete!"
echo "================================================="
echo ""
echo "  Dashboard:  http://localhost:${KLEITOS_PORT}/dashboard"
echo "  API:        http://localhost:${KLEITOS_PORT}/api/v1/health"
echo "  Data:       $KLEITOS_DATA"
echo "  Logs:       $LOG_DIR"
echo ""
echo "  Commands:"
echo "    docker compose logs -f axion    # View logs"
echo "    docker compose restart axion    # Restart"
echo "    docker compose down               # Stop"
echo ""
echo "  Auto-start: Enabled via launchd ($PLIST_NAME)"
echo "    launchctl unload $PLIST_PATH      # Disable auto-start"
echo ""
