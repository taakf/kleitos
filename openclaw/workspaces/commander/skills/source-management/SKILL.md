---
user-invocable: false
---

# Source Management Skill

Manage data sources for event collection.

## Usage

When the user asks about news sources, wants to add/remove sources, or check source health.

## Instructions

### List Sources
```bash
curl -s http://localhost:7777/api/v1/sources | python3 -m json.tool
```

### Create Source
```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"name":"Source Name","domain":"example.com","source_type":"rss","parser_id":"rss_generic"}' \
  http://localhost:7777/api/v1/sources | python3 -m json.tool
```

### Check Source Health
```bash
curl -s http://localhost:7777/api/v1/sources/{source_id}/health | python3 -m json.tool
```

### Enable/Disable Source
```bash
curl -s -X POST http://localhost:7777/api/v1/sources/{source_id}/enable | python3 -m json.tool
curl -s -X POST http://localhost:7777/api/v1/sources/{source_id}/disable | python3 -m json.tool
```

### Delete Source
```bash
curl -s -X DELETE http://localhost:7777/api/v1/sources/{source_id} | python3 -m json.tool
```

## Response Format

- List sources with their status and last fetch time
- Confirm any changes made
- Report health issues if any
