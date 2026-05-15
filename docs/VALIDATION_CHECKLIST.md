# Axion — Validation Checklist

## Windows Clean-Machine Test

Run on a clean Windows 10/11 machine with Python 3.12 installed.

### Setup
1. [ ] Copy Axion folder to Desktop
2. [ ] Double-click `Axion.bat`
3. [ ] Observe: terminal shows setup progress
4. [ ] Wait for "Axion is running!" message (2-5 min)
5. [ ] Browser opens to http://localhost:7777
6. [ ] Dashboard loads with welcome card

### Core Functionality
7. [ ] Create a new portfolio (use + button in nav)
8. [ ] Add 3 holdings manually (e.g., AAPL, MSFT, GOOG)
9. [ ] Switch to default portfolio — verify original data
10. [ ] Switch back — verify new portfolio data
11. [ ] Upload `sample_portfolio.csv` to a portfolio
12. [ ] Review and confirm import
13. [ ] Navigate all tabs: Portfolio, Insights, Alerts, Assistant, Settings

### Intelligence
14. [ ] Go to Settings → News Sources → Quick Add → add Federal Reserve
15. [ ] Trigger collection (Assistant → "Run collection")
16. [ ] Wait 2 min → check Insights → News sub-tab for new news items
17. [ ] Trigger risk assessment (via Assistant or Settings health)
18. [ ] Check Alerts tab — verify alerts match active portfolio

### Persistence
19. [ ] Close browser and terminal window
20. [ ] Double-click Axion.bat again — should detect already running
21. [ ] Restart computer → verify auto-start (Axion should be running)

### Edge Cases
22. [ ] Test with folder on path containing spaces (e.g., `C:\Users\John Smith\Axion`)
23. [ ] Note any SmartScreen warnings — document exact message

### Pass Criteria
- All 23 checks pass
- No terminal errors visible to user
- Multi-portfolio isolation holds
- Data persists across restart

---

## macOS Clean-Machine Test

Run on a Mac (Apple Silicon preferred).

### Setup
1. [ ] Double-click Axion.app
2. [ ] Handle Gatekeeper: right-click → Open → Open (first time only)
3. [ ] Observe: "Starting Axion..." notification appears
4. [ ] Wait for dashboard to open (2-5 min on first launch)

### Core Functionality
5. [ ] Create new portfolio via + button
6. [ ] Add holdings, verify isolation between portfolios
7. [ ] Upload CSV, complete review/import flow
8. [ ] Navigate all tabs

### Intelligence
9. [ ] Add sources via Settings → News Sources
10. [ ] Trigger collection
11. [ ] Verify events appear
12. [ ] Trigger risk assessment, verify per-portfolio alerts
13. [ ] Generate digest, verify per-portfolio content

### Persistence
14. [ ] Close window → verify server keeps running (curl health)
15. [ ] Relaunch Axion.app → should open dashboard immediately
16. [ ] Log out/in → verify auto-start via launchd

---

## AI Provider Test

Requires a working API key with active credits.

### Setup
1. [ ] Go to Settings → AI Provider → select Anthropic (or OpenAI/Google)
2. [ ] Enter API key → Save Key
3. [ ] Observe: status shows "configured (restart required)"
4. [ ] Restart Axion → verify status shows "AI" (green dot)

### Verification
5. [ ] Go to Settings → System Health → click "Test Provider"
   - Or: `curl -X POST http://localhost:7777/api/v1/settings/test-provider`
   - Expected: `{"status": "active", "provider": "anthropic"}`
6. [ ] Go to Assistant → ask "What are my top risks?"
   - Expected: AI-generated response (not rule-based)
7. [ ] Trigger analysis: `curl -X POST http://localhost:7777/api/v1/agents/analysis/run`
   - Wait 30s → check Insights → Analysis tab
8. [ ] Generate digest with AI content
   - Check Insights → Digest tab

### Pass Criteria
- Test provider returns "active"
- Assistant shows AI mode (green dot)
- Analysis notes contain real AI-generated content
- Digest contains AI-generated summaries

---

## 24-Hour Soak Test

Run on the target deployment machine.

### Setup
Start Axion normally. Create at least 2 portfolios with holdings.

### What to Check After 24 Hours

```bash
# Health
curl -s http://localhost:7777/api/v1/health | python3 -m json.tool

# Expected: status=ok, scheduler=running, sources_healthy > 0

# Agent runs (should show multiple completed runs)
curl -s http://localhost:7777/api/v1/agents/runs | python3 -c "
import json,sys
runs = json.load(sys.stdin)
if isinstance(runs, list):
    for r in runs[:10]:
        print(f'{r[\"agent_id\"]:15s} {r[\"status\"]:10s} errors={r[\"items_failed\"]}')
"

# Event count (should be growing)
curl -s 'http://localhost:7777/api/v1/events?limit=1' -o /dev/null -w 'Events available\n'

# DB size (should be <50MB for normal use)
ls -lh ~/axion-data/db/kleitos.db 2>/dev/null || ls -lh ~/kleitos-data/db/kleitos.db

# Log size (should not grow unbounded)
ls -lh ~/axion-data/logs/ 2>/dev/null || ls -lh ~/kleitos-data/logs/

# Alert count per portfolio (should not have duplicates)
curl -s 'http://localhost:7777/api/v1/alerts?limit=500' | python3 -c "
import json,sys
alerts = json.load(sys.stdin)
if isinstance(alerts, list):
    titles = [a['title'] for a in alerts]
    print(f'Total: {len(alerts)}, Unique: {len(set(titles))}, Dupes: {len(alerts)-len(set(titles))}')
"
```

### Pass Criteria
- Server is still running (health returns ok)
- Scheduler has fired collection + risk + digest multiple times
- No agent runs with errors > 0
- DB size is reasonable (<50MB)
- Log files are not growing unbounded
- No duplicate alerts (title uniqueness holds)
- No cross-portfolio contamination in alerts/digests
