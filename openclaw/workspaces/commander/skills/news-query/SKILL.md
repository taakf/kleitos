---
user-invocable: false
---

# News Query Skill

Query the Axion API for news events affecting portfolio holdings.

## Usage

When the user asks about news, events, or developments affecting their portfolio or specific holdings, use this skill.

## Instructions

### Recent Events (last 24 hours)
```bash
curl -s http://localhost:7777/api/v1/events/recent | python3 -m json.tool
```

### Events for Specific Ticker
```bash
curl -s "http://localhost:7777/api/v1/events?ticker=TICKER&days=7" | python3 -m json.tool
```

### Events by Materiality
```bash
curl -s "http://localhost:7777/api/v1/events?materiality=important&days=7" | python3 -m json.tool
```

### Events by Type
```bash
curl -s "http://localhost:7777/api/v1/events?event_type=earnings&days=30" | python3 -m json.tool
```

### Event Detail with Analysis
```bash
curl -s "http://localhost:7777/api/v1/events/EVENT_ID" | python3 -m json.tool
```

## Response Format

Organize events by materiality:
1. CRITICAL events first
2. IMPORTANT events second
3. WATCH items third

For each event include:
- Title and summary
- Which holdings are affected
- Why it matters (impact channel)
- Source attribution

Never dump raw JSON. Format professionally with clear structure.
