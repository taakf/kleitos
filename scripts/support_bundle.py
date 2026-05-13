#!/usr/bin/env python3
"""
Generate a redacted Axion support bundle.

The output is a single zip in ``<data_dir>/support/`` that a customer can
attach to a support email. Everything potentially sensitive is redacted
or excluded:

  - **Excluded:** `.db` files, backup `.db` files, raw `.env`, raw API keys
    or secrets, holdings values, portfolio names.
  - **Included:** structured diagnostics (counts + paths), redacted env
    var snapshot, redacted settings summary, last N lines of each log
    file, schema version, list of backup filenames (not contents),
    requirements.txt, git commit if available, Python + platform info.

The script does not require the server to be running — it pulls
everything via raw sqlite3 and filesystem reads.

Usage
-----
    python scripts/support_bundle.py
    python scripts/support_bundle.py --data-dir /path/to/data
    python scripts/support_bundle.py --output /tmp/axion-support.zip

Exit code 0 on success, non-zero on hard error.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sqlite3
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ─── Redaction rules ──────────────────────────────────────────────────────
#
# Env vars whose **value** must never be printed. Match on key name first
# (case-insensitive substring), then fall back to value-level regex for
# obvious patterns. Names are checked before regex so we don't accidentally
# leak a key by name even if the value happens to not match a known
# pattern.

_SECRET_KEY_SUBSTRINGS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "AUTH",
    "CREDENTIAL",
    "API_KEY",
    "SMTP_PASS",
)

# Value patterns that look like secrets even when the env-var name is benign.
_SECRET_VALUE_PATTERNS = (
    re.compile(r"^sk-ant-[a-zA-Z0-9_\-]{16,}$"),          # Anthropic
    re.compile(r"^sk-proj-[a-zA-Z0-9_\-]{16,}$"),         # OpenAI project
    re.compile(r"^sk-[a-zA-Z0-9_\-]{20,}$"),              # OpenAI legacy
    re.compile(r"^AIza[a-zA-Z0-9_\-]{20,}$"),             # Google
    re.compile(r"^ghp_[a-zA-Z0-9]{20,}$"),                # GitHub PAT
    re.compile(r"^gho_[a-zA-Z0-9]{20,}$"),                # GitHub OAuth
    re.compile(r"^xox[bpars]-[a-zA-Z0-9_\-]{20,}$"),      # Slack
    re.compile(r"^[0-9]{8,}:[a-zA-Z0-9_\-]{30,}$"),       # Telegram bot token
)


def _redact_value(key: str, value: str) -> str:
    """Return a redacted version of ``value`` if it looks sensitive."""
    if not value:
        return value
    upper_key = key.upper()
    for needle in _SECRET_KEY_SUBSTRINGS:
        if needle in upper_key:
            return f"<redacted ({len(value)} chars)>"
    for pat in _SECRET_VALUE_PATTERNS:
        if pat.match(value):
            return f"<redacted ({len(value)} chars)>"
    return value


def _redact_env(env: dict[str, str]) -> dict[str, str]:
    """Return a copy of ``env`` with secret values masked."""
    return {k: _redact_value(k, v) for k, v in env.items()}


# ─── Collection helpers ──────────────────────────────────────────────────


def _git_commit() -> str | None:
    """Best-effort git commit lookup. Returns None if not a repo."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _git_describe() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "describe", "--tags", "--always", "--dirty"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _tail(path: Path, max_bytes: int = 200_000) -> str:
    """Return up to the last ``max_bytes`` of ``path`` (≈200 KB by default)."""
    if not path.exists() or not path.is_file():
        return ""
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    try:
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                # Skip a partial first line so the result is line-aligned.
                f.readline()
            data = f.read()
        return data.decode("utf-8", errors="replace")
    except OSError as exc:
        return f"<unable to read {path}: {exc}>"


def _safe_count(cur: sqlite3.Cursor, sql: str) -> int | None:
    try:
        row = cur.execute(sql).fetchone()
        return int(row[0]) if row else None
    except sqlite3.DatabaseError:
        return None


def _collect_db_diagnostics(db_path: Path) -> dict:
    """Read counts + schema version from the DB via raw sqlite3 (read-only)."""
    out: dict = {
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "db_size_bytes": None,
        "schema_version": None,
        "tables": {},
        "errors": [],
    }
    if not db_path.exists() or db_path.stat().st_size == 0:
        return out
    try:
        out["db_size_bytes"] = db_path.stat().st_size
    except OSError as exc:
        out["errors"].append(f"stat failed: {exc}")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            # Schema version
            try:
                row = cur.execute(
                    "SELECT version FROM _schema_version WHERE id=1"
                ).fetchone()
                out["schema_version"] = int(row[0]) if row else None
            except sqlite3.DatabaseError as exc:
                out["errors"].append(f"schema_version read: {exc}")

            for tbl in [
                "portfolios",
                "holdings",
                "trades",
                "securities",
                "sources",
                "events",
                "event_links",
                "alerts",
                "digests",
                "analysis_notes",
                "audit_log",
                "agent_runs",
                "coverage_reports",
                "holding_factor_sensitivities",
                "macro_factor_events",
                "holding_relationships",
                "telegram_sessions",
                "telegram_deliveries",
                "notification_reads",
                "action_states",
                "saved_views",
            ]:
                out["tables"][tbl] = _safe_count(cur, f"SELECT COUNT(*) FROM {tbl}")
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        out["errors"].append(f"db open failed: {exc}")
    return out


def _list_backups(backup_dir: Path) -> list[dict]:
    """Filenames + sizes only. Never contents."""
    if not backup_dir.exists():
        return []
    out: list[dict] = []
    for path in sorted(backup_dir.glob("*.db")):
        try:
            stat = path.stat()
            out.append({
                "name": path.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            })
        except OSError as exc:
            out.append({"name": path.name, "error": str(exc)})
    return out


def _list_log_files(log_dir: Path) -> list[dict]:
    if not log_dir.exists():
        return []
    out: list[dict] = []
    for path in sorted(log_dir.iterdir()):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            out.append({
                "name": path.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            })
        except OSError as exc:
            out.append({"name": path.name, "error": str(exc)})
    return out


# ─── Bundle assembly ─────────────────────────────────────────────────────


def build_bundle(data_dir: Path, output_path: Path) -> dict:
    """Build the support bundle. Returns a summary dict."""
    db_path = data_dir / "db" / "kleitos.db"
    backup_dir = data_dir / "backups"
    log_dir = data_dir / "logs"
    support_dir = data_dir / "support"
    support_dir.mkdir(parents=True, exist_ok=True)

    # Collect everything first so a partial collection still ships.
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app": "Axion",
        "git_commit": _git_commit(),
        "git_describe": _git_describe(),
        "python_version": (
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "argv": sys.argv,
        "cwd": str(Path.cwd()),
        "project_root": str(PROJECT_ROOT),
        "data_dir": str(data_dir),
        "db_path": str(db_path),
        "backup_dir": str(backup_dir),
        "log_dir": str(log_dir),
    }

    # Pull schema version from migrations module (independent of DB).
    try:
        from src.database.migrations import CURRENT_SCHEMA_VERSION as _CSV
        metadata["app_supported_schema_version"] = _CSV
    except Exception as exc:
        metadata["app_supported_schema_version"] = None
        metadata["app_supported_schema_version_error"] = str(exc)

    env_snapshot = _redact_env({k: v for k, v in os.environ.items()})

    diagnostics_db = _collect_db_diagnostics(db_path)
    backups = _list_backups(backup_dir)
    logs = _list_log_files(log_dir)

    # Settings summary via the config loader, with redaction. Never include
    # raw secrets. The config layer uses SecretStr for keys so they print as
    # "**********" — we additionally redact by structure.
    settings_dump: dict | None = None
    try:
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()

        def _redact_settings(node):
            if isinstance(node, dict):
                return {k: _redact_settings(v) for k, v in node.items()}
            if isinstance(node, list):
                return [_redact_settings(x) for x in node]
            if isinstance(node, str) and node and len(node) > 8:
                # Best-effort heuristic. We're already conservative in env-redaction.
                if any(p.match(node) for p in _SECRET_VALUE_PATTERNS):
                    return f"<redacted ({len(node)} chars)>"
            return node

        # For pydantic SecretStr: model_dump leaves SecretStr objects as
        # SecretStr unless mode="json". Force JSON-friendly first, then redact.
        raw_json = s.model_dump_json()
        settings_dump = _redact_settings(json.loads(raw_json))
    except Exception as exc:
        settings_dump = {"error": str(exc)}

    # Write the zip
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("metadata.json", json.dumps(metadata, indent=2))
        zf.writestr("environment.redacted.json", json.dumps(env_snapshot, indent=2))
        zf.writestr("db_diagnostics.json", json.dumps(diagnostics_db, indent=2))
        zf.writestr("backups.json", json.dumps(backups, indent=2))
        zf.writestr("logs_index.json", json.dumps(logs, indent=2))
        zf.writestr(
            "settings.redacted.json",
            json.dumps(settings_dump or {"error": "unavailable"}, indent=2),
        )

        # Last 200 KB of each log file (never the .db files).
        for log_meta in logs:
            name = log_meta.get("name")
            if not name:
                continue
            path = log_dir / name
            tail = _tail(path)
            zf.writestr(f"logs/{name}", tail)

        # requirements.txt for diff-against-installed
        req = PROJECT_ROOT / "requirements.txt"
        if req.exists():
            try:
                zf.writestr("requirements.txt", req.read_text(encoding="utf-8"))
            except OSError:
                pass

        # README of the bundle itself, in plain text.
        zf.writestr(
            "README.txt",
            (
                "Axion support bundle\n"
                "====================\n\n"
                "This zip is a redacted snapshot of your Axion installation. It contains:\n\n"
                "  metadata.json              app + platform info\n"
                "  environment.redacted.json  env vars (secrets masked)\n"
                "  db_diagnostics.json        schema version + table counts\n"
                "  backups.json               filenames + sizes of pre-upgrade backups\n"
                "  logs_index.json            list of log files in the data dir\n"
                "  logs/                      last ~200KB of each log file\n"
                "  settings.redacted.json     loaded settings (secrets masked)\n"
                "  requirements.txt           expected dependencies\n\n"
                "What is NOT included (by design):\n"
                "  - your database file (kleitos.db) or any backup .db files\n"
                "  - the raw contents of ~/.axion.env\n"
                "  - API keys, tokens, or other secrets\n"
                "  - portfolio names, holdings, or transactions\n"
            ),
        )

    return {
        "output_path": str(output_path),
        "metadata": metadata,
        "diagnostics_db": diagnostics_db,
        "backup_count": len(backups),
        "log_count": len(logs),
    }


def _resolve_data_dir() -> Path:
    if env := os.environ.get("AXION_DATA_DIR"):
        return Path(env)
    if env := os.environ.get("KLEITOS_DATA_DIR"):
        return Path(env)
    home = Path.home()
    kleitos = home / "kleitos-data"
    axion = home / "axion-data"
    if kleitos.exists() and not axion.exists():
        return kleitos
    return axion


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an Axion support bundle.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Axion data directory (default: AXION_DATA_DIR or ~/axion-data).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output zip path (default: <data_dir>/support/axion-support-<ts>.zip).",
    )
    args = parser.parse_args()

    data_dir = (args.data_dir or _resolve_data_dir()).expanduser().resolve()
    if not data_dir.exists():
        # Don't refuse — create the dir so the bundle script is useful even
        # before first launch.
        data_dir.mkdir(parents=True, exist_ok=True)

    if args.output is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out = data_dir / "support" / f"axion-support-{ts}.zip"
    else:
        out = args.output.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"Building support bundle for data dir: {data_dir}")
    summary = build_bundle(data_dir, out)
    print(f"Support bundle written: {out}")
    print(f"  size: {out.stat().st_size:,} bytes")
    print(f"  backup files referenced: {summary['backup_count']}")
    print(f"  log files referenced:    {summary['log_count']}")
    if summary["diagnostics_db"].get("schema_version") is not None:
        print(f"  DB schema version:       {summary['diagnostics_db']['schema_version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
