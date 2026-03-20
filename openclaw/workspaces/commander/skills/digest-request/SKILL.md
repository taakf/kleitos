---
user-invocable: true
---

# Digest Skill

/digest - Get the latest portfolio intelligence digest or generate a new one.

## Usage

When the user asks for a digest, briefing, summary, or morning update.

## Instructions

### Get Latest Digest
```bash
curl -s http://localhost:7777/api/v1/digests/latest | python3 -m json.tool
```

### Generate Fresh Digest
```bash
curl -s -X POST http://localhost:7777/api/v1/digests/generate | python3 -m json.tool
```

## Response Format

Present the digest in this structure:

```
PORTFOLIO DIGEST — [Date]

MATERIAL DEVELOPMENTS
━━━━━━━━━━━━━━━━━━━━━
[CRITICAL/IMPORTANT] Event title
  → Affects: [holdings]
  → Impact: [explanation]
  → Source: [source]

WATCH LIST
━━━━━━━━━━
• Item with brief explanation

PORTFOLIO SNAPSHOT
━━━━━━━━━━━━━━━━━
Holdings: N | Active Alerts: N | Sources: N
```
