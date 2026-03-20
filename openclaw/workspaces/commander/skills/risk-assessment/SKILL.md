---
user-invocable: false
---

# Risk Assessment Skill

Trigger a risk assessment and review risk alerts.

## Usage

When the user asks about risk, concentration, or portfolio safety, use this skill.

## Instructions

### Run Risk Check
```bash
curl -s -X POST http://localhost:7777/api/v1/agents/risk/run | python3 -m json.tool
```

### View Active Alerts
```bash
curl -s http://localhost:7777/api/v1/alerts/active | python3 -m json.tool
```

### View All Alerts by Severity
```bash
curl -s "http://localhost:7777/api/v1/alerts?severity=critical" | python3 -m json.tool
curl -s "http://localhost:7777/api/v1/alerts?severity=high" | python3 -m json.tool
```

### Acknowledge Alert
```bash
curl -s -X POST http://localhost:7777/api/v1/alerts/{alert_id}/acknowledge | python3 -m json.tool
```

### Bulk Acknowledge All
```bash
curl -s -X POST http://localhost:7777/api/v1/alerts/acknowledge-all | python3 -m json.tool
```

## Response Format

- Organize alerts by severity (critical first)
- Explain each risk clearly: what it is, why it matters, what threshold was breached
- Include the holding/sector/geography involved
- Suggest what to monitor (not what to trade)
