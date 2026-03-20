---
user-invocable: false
---

# Portfolio Upload Skill

Upload portfolio data via CSV file.

## Usage

When the user wants to upload or update their portfolio holdings.

## Instructions

### Upload CSV
```bash
curl -s -X POST -F "file=@/path/to/portfolio.csv" http://localhost:7777/api/v1/portfolio/upload | python3 -m json.tool
```

### Verify Upload
```bash
curl -s http://localhost:7777/api/v1/portfolio/holdings | python3 -m json.tool
curl -s http://localhost:7777/api/v1/portfolio/summary | python3 -m json.tool
```

## Expected CSV Format

```csv
ticker,quantity,cost_basis,currency
AAPL,100,150.00,USD
MSFT,50,300.00,USD
```

## Response Format

- Confirm how many holdings were imported/updated
- Report any errors or conflicts
- Show the updated portfolio summary
