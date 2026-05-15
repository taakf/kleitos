#!/usr/bin/env python3
"""
Run Axion database migrations with customer-friendly output.

Launchers call this script before starting uvicorn. It surfaces the typed
errors defined in ``src.database.migrations`` as clean, copy-pasteable
messages with structured exit codes — never a raw Python traceback on the
customer console (the traceback still reaches developer logs at WARNING).

Exit codes
----------
  0   migrations succeeded (or DB is already at head)
  1   unexpected error
  2   DB schema is newer than this app supports
  3   DB file is corrupt or unreadable
  4   pre-migration backup failed

Usage
-----
    python scripts/migrate.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import traceback
from pathlib import Path

# Make ``src.*`` importable when this script is invoked directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

EXIT_OK = 0
EXIT_OTHER = 1
EXIT_VERSION_TOO_NEW = 2
EXIT_CORRUPT = 3
EXIT_BACKUP_FAILED = 4


def _banner(title: str) -> None:
    line = "=" * 64
    print(line)
    print(f"  {title}")
    print(line)


def main() -> int:
    # Customer console: WARNING+ only. Tracebacks go to the logger, not stdout.
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s | %(name)s | %(message)s",
    )

    try:
        from src.database.migrations import (
            BackupFailedError,
            DatabaseCorruptError,
            DatabaseVersionTooNewError,
            run_migrations,
        )
    except Exception as exc:  # pragma: no cover — import-time failure
        _banner("Axion — unable to import migration module")
        print(f"  {type(exc).__name__}: {exc}")
        print()
        return EXIT_OTHER

    try:
        asyncio.run(run_migrations())
        print("migrations: ok")
        return EXIT_OK

    except DatabaseVersionTooNewError as exc:
        _banner("Axion — newer database detected")
        print()
        print( "  Your Axion data was created by a newer version of Axion.")
        print(f"  This version supports schema v{exc.app_version}, but your")
        print(f"  database is at v{exc.db_version}.")
        print()
        print(f"  Database file : {exc.db_path}")
        print(f"  Data dir      : {exc.data_dir}")
        print(f"  Backups       : {exc.backup_dir}")
        print()
        print( "  Your data has not been modified.")
        print()
        print( "  Next steps:")
        print(f"    1. Update Axion to a version that supports schema v{exc.db_version},")
        print( "       OR")
        print(f"    2. Restore a compatible backup from {exc.backup_dir}.")
        print()
        return EXIT_VERSION_TOO_NEW

    except DatabaseCorruptError as exc:
        _banner("Axion — could not open the database")
        print()
        print( "  The database file appears to be corrupt or unreadable.")
        print( "  Axion has NOT modified the file.")
        print()
        print(f"  Database file : {exc.db_path}")
        print(f"  Backups       : {exc.backup_dir}")
        print()
        print( "  Next steps:")
        print(f"    1. Restore a backup from {exc.backup_dir}, OR")
        print( "    2. Move the database file aside and relaunch Axion to start")
        print( "       with a fresh database (your portfolios + holdings will be empty).")
        if exc.original_error is not None:
            print()
            print(f"  Developer detail: {exc.original_error}")
        print()
        return EXIT_CORRUPT

    except BackupFailedError as exc:
        _banner("Axion — pre-migration backup failed")
        print()
        print( "  Before upgrading the database to the new schema, Axion tried")
        print( "  to create a safety backup. The backup failed.")
        print( "  No schema changes were applied — your data is unchanged.")
        print()
        print(f"  Database file   : {exc.db_path}")
        print(f"  Attempted backup: {exc.backup_path}")
        print(f"  Reason          : {exc.reason}")
        print()
        print( "  Next steps:")
        print( "    1. Free disk space or fix folder permissions on the backups directory.")
        print( "    2. Relaunch Axion.")
        print()
        return EXIT_BACKUP_FAILED

    except Exception as exc:
        _banner("Axion — unexpected migration error")
        print()
        print(f"  {type(exc).__name__}: {exc}")
        print()
        print( "  This is a developer-facing error. Re-run with AXION_LOG_LEVEL=DEBUG")
        print( "  for the full traceback, or inspect the data-dir logs folder.")
        # Surface traceback to logs (still hidden from the customer console
        # unless they raised the log level).
        logging.warning("migration failure traceback:\n%s", traceback.format_exc())
        return EXIT_OTHER


if __name__ == "__main__":
    sys.exit(main())
