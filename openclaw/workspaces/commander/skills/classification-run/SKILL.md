---
user-invocable: false
---

# Classification Run Skill

Trigger security classification for unclassified holdings.

## Usage

When the user asks to classify holdings, update sectors, or check classification status.

## Instructions

### Run Classification
```bash
curl -s -X POST http://localhost:7777/api/v1/agents/classification/run | python3 -m json.tool
```

### Check Current Exposures
```bash
curl -s "http://localhost:7777/api/v1/portfolio/exposure?dimension=sector" | python3 -m json.tool
curl -s "http://localhost:7777/api/v1/portfolio/exposure?dimension=geography" | python3 -m json.tool
```

## Response Format

- Report how many securities were classified
- Show the resulting sector/geography breakdown
- Note any that failed or were skipped
