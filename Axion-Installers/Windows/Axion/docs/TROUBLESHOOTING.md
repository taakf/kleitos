# Troubleshooting

## Service Won't Start

**Symptom**: `./start.sh` fails or `status.sh` shows not running.

**Check**:
```bash
# View stderr
cat ~/kleitos-data/logs/stderr.log

# Check port availability
lsof -i :7777

# Try running directly
cd ~/axion
source venv/bin/activate
python -m uvicorn src.main:app --host 127.0.0.1 --port 7777
```

**Common causes**:
- Port 7777 already in use → kill the process or change port in settings
- Missing `~/.kleitos.env` → copy from `.env.template`
- Python venv missing → re-run `./install.sh`
- Missing dependencies → `source venv/bin/activate && pip install -r requirements.txt`

## No Events Being Collected

**Symptom**: `GET /api/events/recent` returns empty.

**Check**:
```bash
# Are any sources enabled?
curl http://localhost:7777/api/v1/sources | python3 -m json.tool

# Check scheduler jobs
curl http://localhost:7777/api/v1/health | python3 -m json.tool

# Look for fetch errors
grep -i "error\|failed\|timeout" ~/kleitos-data/logs/kleitos.log | tail -20
```

**Common causes**:
- All sources disabled → enable at least one in `config/sources.yaml`
- API keys not set → check `~/.kleitos.env` for `NEWSAPI_KEY`, `FINNHUB_KEY`
- Rate limiting → check logs for 429 responses, fetcher backs off automatically
- Network issues → test: `curl -s https://www.investing.com/rss/news.rss | head -5`

## LLM Scoring Not Working

**Symptom**: Events have rule matches but no impact scores.

**Check**:
```bash
# Verify API key
grep ANTHROPIC_API_KEY ~/.kleitos.env

# Check for API errors
grep -i "anthropic\|claude\|llm" ~/kleitos-data/logs/kleitos.log | tail -20
```

**Common causes**:
- `ANTHROPIC_API_KEY` not set or invalid
- Rate limit exceeded → system retries automatically with backoff
- Model not available → check `config/settings.yaml` under `llm.model`

## Database Issues

**Symptom**: API returns 500 errors, "database locked" in logs.

**Check**:
```bash
# Verify DB exists and isn't corrupted
sqlite3 ~/kleitos-data/db/kleitos.db "PRAGMA integrity_check;"

# Check WAL mode
sqlite3 ~/kleitos-data/db/kleitos.db "PRAGMA journal_mode;"
# Should return: wal
```

**Fix**:
```bash
# If corrupted, restore from backup
./stop.sh
./scripts/restore.sh ~/kleitos-data/backups/kleitos_latest.db
./start.sh
```

**Prevention**: WAL mode allows concurrent reads. Database locking typically means a long-running write – check for stuck scheduler jobs.

## Portfolio Upload Fails

**Symptom**: CSV upload returns error.

**Check**: Ensure your CSV has the required columns:
```
ticker,quantity,price,currency
AAPL,100,180.50,USD
```

**Supported column names**:
- Ticker: `ticker`, `symbol`, `stock`
- Quantity: `quantity`, `shares`, `qty`, `units`
- Price: `price`, `current_price`, `last_price`, `close`
- Currency: `currency`, `ccy`
- ISIN: `isin` (optional)

**Common causes**:
- Wrong delimiter → must be comma-separated
- Missing required column → at least `ticker` and `quantity` needed
- Encoding issues → use UTF-8

## Dashboard Not Loading

**Symptom**: `http://localhost:7777/dashboard` shows blank or errors.

**Check**:
```bash
# Is the API running?
curl http://localhost:7777/api/v1/health

# Check static files
ls ~/axion/dashboard/index.html
```

**Common causes**:
- Static file mount not configured → check `src/main.py`
- CORS issues → API should serve dashboard from same origin

## OpenClaw Not Responding

**Symptom**: Chat commands don't produce responses.

**Check**:
```bash
# Is OpenClaw running?
launchctl list | grep openclaw

# Is the Axion API reachable from OpenClaw?
curl http://localhost:7777/api/v1/health
```

**Common causes**:
- OpenClaw not started → `launchctl load ~/Library/LaunchAgents/com.axion.openclaw.plist`
- Workspace configs not copied → re-run the OpenClaw setup
- Skills referencing wrong API URL → check `curl` commands in SKILL.md files

## High Memory Usage

**Symptom**: Process using >500MB RAM.

**Check**:
```bash
ps aux | grep uvicorn
```

**Mitigations**:
- Reduce scheduler frequency in `config/settings.yaml`
- Limit concurrent source fetches (collection agent processes sequentially by default)
- Prune old events: `sqlite3 ~/kleitos-data/db/kleitos.db "DELETE FROM events WHERE ingested_at < date('now', '-90 days');"`

## Reset Everything

If all else fails:

```bash
./stop.sh
# Backup data first
cp ~/kleitos-data/db/kleitos.db ~/kleitos-data/db/kleitos.db.bak
# Re-initialise
rm ~/kleitos-data/db/kleitos.db
./install.sh
./start.sh
```

Your portfolio will need to be re-uploaded, but source configs are preserved in `config/sources.yaml`.
