# Axion — Multi-Portfolio Architecture

## Overview

Axion supports multiple named portfolios. Each portfolio contains its own
holdings, trades, alerts, and digests. News events and sources are shared
globally across all portfolios.

## Data Model

### Portfolio-Scoped Entities (per-portfolio)

| Entity | How Scoped | Notes |
|--------|-----------|-------|
| **Portfolio** | Master entity | id, name, description, base_currency, is_default |
| **Holding** | `portfolio_id` FK | Each holding belongs to exactly one portfolio |
| **Trade** | Via holding FK | Trades link to holdings, which belong to a portfolio |
| **Alert** | `portfolio_id` column | Risk alerts are generated per-portfolio |
| **Digest** | `portfolio_id` column | Intelligence digests are per-portfolio |
| **AnalysisNote** | Via `holding_id` FK | Inherits portfolio scope from the linked holding |
| **CoverageReport** | Via `holding_id` FK | Inherits portfolio scope from the linked holding |

### Global Entities (shared across portfolios)

| Entity | Why Global |
|--------|-----------|
| **Event** | News is not portfolio-specific |
| **EventLink** | Links events to holdings (already portfolio-scoped) |
| **Source** | News sources are shared |
| **Security** | Reference data is shared |
| **AuditLog** | System-wide audit trail |
| **AgentRun** | System operations |

## Default Portfolio

Every Axion install has a **default portfolio** with:
- `id = "default"`
- `name = "Main Portfolio"`
- `is_default = 1`

The default portfolio:
- Cannot be deleted
- Is used when no `portfolio_id` is specified in API calls
- Is created automatically on fresh installs and during v1 → v2 migration

## API Usage

### Portfolio Management

```
GET    /api/v1/portfolios              — list all portfolios
POST   /api/v1/portfolios              — create new portfolio
GET    /api/v1/portfolios/{id}         — get portfolio details
PUT    /api/v1/portfolios/{id}         — update name/description
DELETE /api/v1/portfolios/{id}         — delete (must be empty, non-default)
```

### Portfolio-Scoped Endpoints

All portfolio-scoped read endpoints accept an optional `?portfolio_id=` parameter.
If omitted, they default to the default portfolio.

```
GET /api/v1/portfolio/holdings?portfolio_id=default
GET /api/v1/portfolio/summary?portfolio_id=abc123
GET /api/v1/portfolio/exposure?dimension=sector&portfolio_id=abc123
GET /api/v1/portfolio/trades?portfolio_id=abc123
```

### Creating Holdings in a Specific Portfolio

```json
POST /api/v1/portfolio/holdings
{
  "ticker": "AAPL",
  "quantity": 100,
  "portfolio_id": "abc123"
}
```

## Migration Behavior

### Fresh Install
- All tables created
- Default "Main Portfolio" inserted automatically
- Schema version set to 2

### Existing v1 Install
- `portfolios` table created
- Default "Main Portfolio" inserted with `id='default'`
- All existing holdings migrated from `portfolio_id='main'` to `portfolio_id='default'`
- `portfolio_id` column added to alerts and digests, backfilled to default
- Schema version updated to 2

## Deletion Rules

- **Default portfolio**: Cannot be deleted (returns 409 error)
- **Non-empty portfolio**: Cannot be deleted if it has active holdings (returns 409 error with holding count)
- **Empty portfolio**: Can be deleted freely
