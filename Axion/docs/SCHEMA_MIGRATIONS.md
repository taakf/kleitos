# Axion — Schema Migration System

## Overview

Axion uses a built-in schema versioning system that runs automatically on
every startup. The current schema version is tracked in a `_schema_version`
table in the SQLite database.

## How It Works

1. **Fresh install**: All tables are created via SQLAlchemy `create_all`, and
   the database is stamped with the current schema version.

2. **Existing install (pre-versioning)**: If tables exist but no
   `_schema_version` table is found, the database is automatically baselined
   at version 1.

3. **Up-to-date**: If the DB version matches the code version, startup
   proceeds normally (fast path).

4. **Upgrade needed**: If the DB version is behind the code version,
   incremental migration steps are applied in order.

5. **Downgrade protection**: If the DB version is *ahead* of the code version
   (e.g. running an older binary against a newer DB), the app refuses to start
   with a clear error message.

## Adding a New Migration

When the schema needs to change (e.g. adding multi-portfolio support):

1. Increment `CURRENT_SCHEMA_VERSION` in `src/database/migrations.py`
2. Write a migration function that takes a synchronous SQLAlchemy connection
3. Register it in the `_MIGRATIONS` list
4. Update the ORM models in `src/database/models.py` to match

Example:

```python
CURRENT_SCHEMA_VERSION = 2

def _migrate_v2(sync_conn):
    sync_conn.execute(text("CREATE TABLE portfolios (...)"))
    sync_conn.execute(text("ALTER TABLE holdings ADD COLUMN ..."))

_MIGRATIONS = [
    (2, "add portfolio table and portfolio_id columns", _migrate_v2),
]
```

## Column-Level Migrations

Simple additive column changes (new nullable columns with defaults) are
handled automatically by `_ensure_columns()`. This function compares the
ORM model definitions against the live schema and issues `ALTER TABLE
ADD COLUMN` statements for any missing columns.

This means you can often add a new optional column to a model without
writing a migration step — just add it to the ORM model and it will be
created on next startup.

## Environment Variables

Schema version is stored in the database, not in configuration. No
environment variables control migration behavior.

## Checking Current Version

```sql
SELECT version, applied_at, description FROM _schema_version;
```
