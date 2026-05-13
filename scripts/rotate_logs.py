#!/usr/bin/env python3
"""
Rotate Axion launcher/server logs.

The launcher invokes this once at startup. It prunes oversized log files
so disk usage stays bounded without forcing the launcher to manage state.

Policy (deliberately simple):
- For each known log filename in <log_dir>, if the file is larger than
  MAX_BYTES, rename it to <name>.1 (rotating <name>.1 → <name>.2 … up to
  KEEP backups), then truncate the live name.
- Backup files older than KEEP are deleted.

This file is also importable as ``rotate_logs.rotate(log_dir)`` so tests
and other scripts can call it without spawning a subprocess.

Usage
-----
    python scripts/rotate_logs.py [<log_dir>]

If <log_dir> is omitted, the script resolves it from the same env-var
fallback the rest of the app uses (AXION_DATA_DIR / KLEITOS_DATA_DIR /
~/axion-data / ~/kleitos-data).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024  # 5 MiB per log file
KEEP = 5  # number of rotated backups to retain per name

# Names this helper knows about. Anything else in the log dir is left alone.
KNOWN_LOG_NAMES = (
    "axion-launcher.log",
    "axion-server.log",
    "axion-migration.log",
    "axion-stdout.log",  # legacy from Axion.app bootstrap
    "axion-stderr.log",  # legacy from Axion.app bootstrap
    "migrate-stdout.log",  # Axion.app migration output
    "migrate-stderr.log",
    "launcher.log",  # legacy
)


def _resolve_log_dir() -> Path:
    """Same fallback the launchers use."""
    if env := os.environ.get("AXION_DATA_DIR"):
        return Path(env) / "logs"
    if env := os.environ.get("KLEITOS_DATA_DIR"):
        return Path(env) / "logs"
    home = Path.home()
    kleitos = home / "kleitos-data"
    axion = home / "axion-data"
    if kleitos.exists() and not axion.exists():
        return kleitos / "logs"
    return axion / "logs"


def _rotate_one(path: Path, keep: int = KEEP) -> int:
    """Rotate ``path`` if it exceeds MAX_BYTES. Returns count of files rotated."""
    if not path.exists() or not path.is_file():
        return 0
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    if size < MAX_BYTES:
        return 0

    # Shift back: foo.4 → foo.5 (delete foo.5 first), foo.3 → foo.4, …
    for i in range(keep, 0, -1):
        src = path.with_suffix(path.suffix + f".{i}")
        dst = path.with_suffix(path.suffix + f".{i + 1}")
        if not src.exists():
            continue
        if i == keep:
            try:
                src.unlink()
            except OSError:
                pass
        else:
            try:
                src.rename(dst)
            except OSError:
                pass

    # Live → foo.1
    rotated_path = path.with_suffix(path.suffix + ".1")
    try:
        path.rename(rotated_path)
    except OSError:
        return 0
    # Recreate the live file empty so subsequent appends work.
    try:
        path.touch()
    except OSError:
        pass
    return 1


def rotate(log_dir: Path | str | None = None) -> dict[str, int]:
    """Rotate every known log file in ``log_dir``. Returns a per-file count."""
    log_dir = Path(log_dir) if log_dir is not None else _resolve_log_dir()
    if not log_dir.exists():
        return {}
    counts: dict[str, int] = {}
    for name in KNOWN_LOG_NAMES:
        counts[name] = _rotate_one(log_dir / name)
    return counts


def main() -> int:
    log_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else _resolve_log_dir()
    counts = rotate(log_dir)
    rotated = sum(counts.values())
    if rotated:
        # Single line, customer-friendly. Launchers can swallow it if they want.
        print(f"rotated {rotated} log file(s) in {log_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
