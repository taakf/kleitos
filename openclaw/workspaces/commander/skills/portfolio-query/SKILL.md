---
user-invocable: false
---

# Portfolio Query Skill

Query the Axion API for portfolio data including holdings, exposures, and summaries.

## Usage

When the user asks about their portfolio, holdings, positions, weights, or exposures, use this skill to fetch the data from the Axion API.

## Instructions

### Holdings Query
```bash
curl -s http://localhost:7777/api/v1/portfolio/holdings | python3 -m json.tool
```

### Single Holding Detail
```bash
curl -s "http://localhost:7777/api/v1/portfolio/holdings?ticker=TICKER" | python3 -m json.tool
```

### Portfolio Summary
```bash
curl -s http://localhost:7777/api/v1/portfolio/summary | python3 -m json.tool
```

### Exposure by Sector
```bash
curl -s "http://localhost:7777/api/v1/portfolio/exposure?dimension=sector" | python3 -m json.tool
```

### Exposure by Geography
```bash
curl -s "http://localhost:7777/api/v1/portfolio/exposure?dimension=geography" | python3 -m json.tool
```

### Exposure by Currency
```bash
curl -s "http://localhost:7777/api/v1/portfolio/exposure?dimension=currency" | python3 -m json.tool
```

### Exposure by Theme
```bash
curl -s "http://localhost:7777/api/v1/portfolio/exposure?dimension=theme" | python3 -m json.tool
```

## Response Format

Format the response as a structured table or list. Include:
- Ticker, name, sector, geography for holdings
- Weight percentages for exposures
- Summary stats (total holdings, total value, top positions)

Never dump raw JSON to the user. Always format it professionally.
