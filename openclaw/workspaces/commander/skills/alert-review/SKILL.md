---
user-invocable: true
---

# Alert Review Skill

/alerts - Review and manage portfolio alerts.

## Usage

When the user asks about alerts, risks, warnings, or wants to acknowledge alerts.

## Instructions

### Get Active Alerts
```bash
curl -s http://localhost:7777/api/v1/alerts/active | python3 -m json.tool
```

### Get All Alerts (including acknowledged)
```bash
curl -s "http://localhost:7777/api/v1/alerts?days=7" | python3 -m json.tool
```

### Acknowledge an Alert
```bash
curl -s -X POST "http://localhost:7777/api/v1/alerts/ALERT_ID/acknowledge" | python3 -m json.tool
```

## Response Format

Present alerts organized by severity:

```
ACTIVE ALERTS
━━━━━━━━━━━━━

[CRITICAL] Alert Title
  Details: explanation
  Holdings: affected holdings
  Action: what to monitor

[WARNING] Alert Title
  Details: explanation

[INFO] Alert Title
  Details: explanation

To acknowledge: say "acknowledge [alert number]"
```
