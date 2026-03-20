# Operations Guide

## Daily Operations

### Check System Status

```bash
./status.sh
# Or via API:
curl http://localhost:7777/api/v1/health
```

### View Latest Digest

```bash
curl http://localhost:7777/api/v1/digests/latest | python3 -m json.tool
```

### Review Active Alerts

```bash
curl http://localhost:7777/api/v1/alerts?status=active | python3 -m json.tool
```

### Acknowledge an Alert

```bash
curl -X POST http://localhost:7777/api/v1/alerts/{alert_id}/acknowledge
```

## Portfolio Management

### Upload New Holdings

```bash
curl -X POST http://localhost:7777/api/v1/portfolio/upload \
  -F "file=@updated_portfolio.csv"
```

### View Current Holdings

```bash
curl http://localhost:7777/api/v1/portfolio/holdings | python3 -m json.tool
```

### View Exposures

```bash
curl http://localhost:7777/api/v1/portfolio/exposures | python3 -m json.tool
```

## Source Management

### List Registered Sources

```bash
curl http://localhost:7777/api/v1/sources | python3 -m json.tool
```

### Enable/Disable a Source

```bash
curl -X POST http://localhost:7777/api/v1/sources/{source_id}/enable
curl -X POST http://localhost:7777/api/v1/sources/{source_id}/disable
```

### Add a New Source

Edit `config/sources.yaml` and add a new entry under `sources:`. Restart the service.

## Scheduled Jobs

| Job | Default Interval | Purpose |
|-----|-------------------|---------|
| Collection | 30 min | Fetch news from all enabled sources |
| Classification | 6 hours | Classify untagged holdings |
| Coverage QA | 4 hours | Check holdings have recent coverage |
| Risk Check | 1 hour | Evaluate concentration and alerts |
| Daily Digest | 07:00 | Generate morning summary |
| Health Check | 5 min | Verify system health |
| Backup | 02:00 | Database backup with 7-day retention |

Intervals are configured in `config/settings.yaml` under `scheduler:`.

## Backups

### Manual Backup

```bash
./scripts/backup.sh
```

Backups are stored in `~/kleitos-data/backups/` with timestamps.

### Restore from Backup

```bash
./scripts/restore.sh ~/kleitos-data/backups/kleitos_YYYYMMDD_HHMMSS.db
```

### Backup Retention

Automatic backups keep the last 7 days. Adjust in `config/settings.yaml`.

## Logs

Logs are written to `~/kleitos-data/logs/`:

- `kleitos.log` – main application log (rotated daily, 7-day retention)
- `stdout.log` / `stderr.log` – launchd service output

### View Recent Logs

```bash
tail -f ~/kleitos-data/logs/kleitos.log
```

### Log Levels

Set in `config/settings.yaml` under `logging.level`. Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`.

## Service Management

### Start/Stop/Restart

```bash
./start.sh     # Start the API server
./stop.sh      # Stop gracefully
./start.sh     # Restart (stops first if running)
```

### Using launchd

```bash
# Start
launchctl load ~/Library/LaunchAgents/com.axion.core.plist

# Stop
launchctl unload ~/Library/LaunchAgents/com.axion.core.plist

# Check status
launchctl list | grep axion
```

## Updates

```bash
./update.sh
```

This pulls latest code, installs new dependencies, runs migrations, and restarts.

## Database

SQLite database lives at `~/kleitos-data/db/kleitos.db`.

### Inspect Directly

```bash
sqlite3 ~/kleitos-data/db/kleitos.db ".tables"
sqlite3 ~/kleitos-data/db/kleitos.db "SELECT COUNT(*) FROM events;"
```

### Audit Trail

Every mutation is logged:

```bash
sqlite3 ~/kleitos-data/db/kleitos.db \
  "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 20;"
```
