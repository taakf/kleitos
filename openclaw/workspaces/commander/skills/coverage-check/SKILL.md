---
user-invocable: false
---

# Coverage Check Skill

Run a coverage QA check and review gaps in event coverage.

## Usage

When the user asks about data quality, coverage gaps, or missing information for holdings.

## Instructions

### Run Coverage QA
```bash
curl -s -X POST http://localhost:7777/api/v1/agents/coverage_qa/run | python3 -m json.tool
```

### View Agent Runs
```bash
curl -s http://localhost:7777/api/v1/agents/runs | python3 -m json.tool
```

## Response Format

- List holdings with gaps
- For each gap, explain what event type is missing (earnings, dividend, analyst, news)
- Include the quality score if available
- Suggest which gaps are most urgent to fill
