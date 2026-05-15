#!/usr/bin/env python3
"""
Axion release-zip builder.

Produces two zip files in `dist/`:

    dist/axion-windows.zip   — Windows download
    dist/axion-macos.zip     — macOS / Linux download

Each zip contains the minimum set of files needed to run Axion locally:

    src/                       application code
    dashboard/                 static UI
    config/                    YAML configs (sources, settings, risk)
    scripts/run_local.{sh,ps1} launchers
    scripts/smoke_local.py     install-verification smoke
    scripts/axion-tray.pyw     (Windows only) tray app
    scripts/axion-menubar.py   (macOS only) menu-bar app
    Axion.bat                  (Windows only) double-click launcher
    Axion.app/                 (macOS only) .app bundle
    requirements.txt
    sample_portfolio.csv
    .env.template
    README.md, README_LOCAL.md
    INSTALL.md, KNOWN_LIMITATIONS.md, OPERATOR_CHECKLIST.md
    docs/                      customer-facing docs
    RELEASE_MANIFEST.json      Phase 17 — generated build receipt

Excluded from every zip:

    .git, .venv, __pycache__, *.pyc, .pytest_cache, .mypy_cache, .ruff_cache
    dist/                      itself
    .claude/, .vscode/, .idea/
    ~/axion-data, *.db, *.log, .env (real secrets file)
    support/, test-results/
    Any path matching the stale-duplicate patterns.

Each zip carries a generated RELEASE_MANIFEST.json at its top level
recording app name / version / release channel / git commit / build
timestamp and an explicit promise that no database files and no API
keys are bundled.

Usage:
    python scripts/build_release_zip.py
    python scripts/build_release_zip.py --output ~/Desktop/axion-release
    python scripts/build_release_zip.py --platform macos
    python scripts/build_release_zip.py --platform windows
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Make ``src`` importable so the manifest can read the single version
# module — without importing the FastAPI app or touching the DB.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── What goes into every platform's zip ──────────────────────────────────────
COMMON_DIRS = ["src", "dashboard", "config", "docs"]

COMMON_FILES = [
    "requirements.txt",
    "pyproject.toml",
    "sample_portfolio.csv",
    ".env.template",
    "README.md",
    "README_LOCAL.md",
    "INSTALL.md",
    "KNOWN_LIMITATIONS.md",
    "OPERATOR_CHECKLIST.md",
    "ARCHITECTURE.md",
    "scripts/run_local.sh",
    "scripts/run_local.ps1",
    "scripts/migrate.py",
    "scripts/rotate_logs.py",
    "scripts/support_bundle.py",
    "scripts/smoke_local.py",
    "scripts/smoke_server_startup.py",
    "scripts/backup.sh",
    "scripts/restore.sh",
]

WINDOWS_ONLY = [
    "Axion.bat",
    "scripts/axion-tray.pyw",
    "scripts/axion-app.pyw",
    "scripts/install-windows.ps1",
    "scripts/uninstall-windows.ps1",
    "scripts/stop-axion.bat",
]

MACOS_ONLY = [
    "scripts/axion-menubar.py",
    "scripts/install-mac.sh",
    "scripts/uninstall-mac.sh",
    "scripts/deploy-mac.sh",
]

MACOS_DIRS = ["Axion.app"]

# ── Path-level exclusions applied to every file walk ─────────────────────────
EXCLUDE_DIR_NAMES = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", ".git",
    ".vscode", ".idea", ".claude", "node_modules",
    "dist", "build", "release", "Axion-Delivery",
    "Axion-Installers", "Axion",  # stale dupes — never ship
    "kleitos-data", "axion-data",
    "support",  # runtime support-bundle output; never ship to a fresh customer
    "test-results",  # Playwright e2e artefacts
}

EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log", ".swp"}

# ``.env`` (the real local secrets file) is excluded by name so a stray
# copy inside a walked directory can never leak.  ``.env.template`` has a
# different name and still ships.
EXCLUDE_FILE_NAMES = {
    ".DS_Store", ".coverage", "server_out.txt", "server_err.txt",
    ".env", ".kleitos.env", ".axion.env",
}

#: The manifest member written into every zip.
RELEASE_MANIFEST_ARCNAME = "RELEASE_MANIFEST.json"


def should_skip(path: Path) -> bool:
    """Return True if this path should NOT be in the zip.

    Only the path RELATIVE to PROJECT_ROOT is considered, so worktree /
    parent directory names don't accidentally trigger exclusions.
    """
    try:
        rel = path.relative_to(PROJECT_ROOT)
    except ValueError:
        # Path is not under the project root — skip defensively.
        return True
    parts = rel.parts
    if any(p in EXCLUDE_DIR_NAMES for p in parts):
        return True
    if path.suffix in EXCLUDE_SUFFIXES:
        return True
    if path.name in EXCLUDE_FILE_NAMES:
        return True
    if path.name.endswith("~"):
        return True
    return False


def iter_files_in_dir(dir_path: Path):
    """Yield (abs_path, arcname_relative) for every file under dir_path."""
    if not dir_path.exists():
        return
    for root, dirs, files in os.walk(dir_path):
        root_path = Path(root)
        # Prune excluded dirs in-place so os.walk doesn't descend into them
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIR_NAMES]
        for fname in files:
            abs_path = root_path / fname
            if should_skip(abs_path):
                continue
            yield abs_path


def add_to_zip(zf: zipfile.ZipFile, src: Path, arcprefix: str, *, label: str | None = None) -> int:
    """Add a file or directory to the zip. Returns count of files added."""
    if not src.exists():
        print(f"  [skip] {label or src.name} (not present)")
        return 0
    count = 0
    if src.is_file():
        if should_skip(src):
            return 0
        arc = f"{arcprefix}/{src.relative_to(PROJECT_ROOT)}"
        zf.write(src, arcname=arc)
        return 1
    for fpath in iter_files_in_dir(src):
        rel = fpath.relative_to(PROJECT_ROOT)
        arc = f"{arcprefix}/{rel}"
        zf.write(fpath, arcname=arc)
        count += 1
    return count


def _git_commit() -> str | None:
    """Return the short git commit, or ``None`` outside a git checkout."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            commit = out.stdout.strip()
            return commit or None
    except Exception:
        pass
    return None


def _release_identity() -> dict:
    """Read the single version module.  Falls back to ``unknown`` so the
    build never fails just because the version module moved."""
    try:
        from src.version import APP_NAME, APP_VERSION, RELEASE_CHANNEL
        return {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "release_channel": RELEASE_CHANNEL,
        }
    except Exception:
        return {
            "app_name": "Axion",
            "app_version": "unknown",
            "release_channel": "local",
        }


def _build_manifest(platform: str, arcnames: list[str]) -> str:
    """Build the RELEASE_MANIFEST.json text written into each zip.

    The manifest is a customer-facing receipt: what this package is,
    when it was built, and an explicit promise that it carries no
    database files and no API keys.
    """
    identity = _release_identity()
    # Top-level runtime entries actually inside the zip (after the
    # ``axion/`` prefix), de-duplicated and sorted.
    top_level = sorted({
        n.split("/", 2)[1]
        for n in arcnames
        if n.startswith("axion/") and len(n.split("/", 2)) > 1 and n.split("/", 2)[1]
    })
    manifest = {
        "app_name": identity["app_name"],
        "app_version": identity["app_version"],
        "release_channel": identity["release_channel"],
        "platform_package": platform,
        "git_commit": _git_commit(),
        "build_timestamp": datetime.now(timezone.utc).isoformat(),
        "file_count": len(arcnames),
        "included_top_level": top_level,
        "guarantees": [
            "No database files (*.db / *.db-wal / *.db-shm) are included.",
            "No API keys, tokens or .env secrets are included — only .env.template.",
            "No customer portfolio data is included — only sample_portfolio.csv.",
            "This is a local, single-machine build (release_channel=local); "
            "it is not a hosted service and performs no broker / OAuth sync.",
        ],
    }
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def build_zip(platform: str, output_dir: Path) -> Path:
    """Build a single platform zip. Returns its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f"axion-{platform}.zip"
    arcprefix = "axion"  # top-level folder inside the zip

    print(f"\n=== Building {zip_path.name} ===")
    if zip_path.exists():
        zip_path.unlink()

    total = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # Common dirs
        for d in COMMON_DIRS:
            n = add_to_zip(zf, PROJECT_ROOT / d, arcprefix, label=d)
            print(f"  {d:<14} {n:4d} file(s)")
            total += n
        # Common files
        for f in COMMON_FILES:
            n = add_to_zip(zf, PROJECT_ROOT / f, arcprefix, label=f)
            total += n
        print(f"  common files   {len(COMMON_FILES)} planned")

        # Platform-specific
        if platform == "windows":
            for f in WINDOWS_ONLY:
                n = add_to_zip(zf, PROJECT_ROOT / f, arcprefix, label=f)
                total += n
        elif platform == "macos":
            for f in MACOS_ONLY:
                n = add_to_zip(zf, PROJECT_ROOT / f, arcprefix, label=f)
                total += n
            for d in MACOS_DIRS:
                n = add_to_zip(zf, PROJECT_ROOT / d, arcprefix, label=d)
                print(f"  {d:<14} {n:4d} file(s)")
                total += n
        else:
            raise ValueError(f"unknown platform: {platform}")

        # Phase 17 — write a release manifest as the last member so the
        # customer (and the verify step) can see exactly what shipped.
        manifest_text = _build_manifest(platform, zf.namelist())
        zf.writestr(f"{arcprefix}/{RELEASE_MANIFEST_ARCNAME}", manifest_text)
        total += 1
        print(f"  manifest       1 file ({RELEASE_MANIFEST_ARCNAME})")

    size_mb = zip_path.stat().st_size / 1024 / 1024
    # Display a project-relative path when the output is inside the
    # repo (the normal ``dist/`` case); fall back to the absolute path
    # when the caller built into an external directory (e.g. a test
    # temp dir).
    try:
        display_path: Path | str = zip_path.relative_to(PROJECT_ROOT)
    except ValueError:
        display_path = zip_path
    print(f"  -> {display_path} ({total} files, {size_mb:.1f} MiB)")
    return zip_path


def verify_zip(zip_path: Path) -> bool:
    """Sanity-check a built zip — verify no stale paths leaked through."""
    print(f"\n=== Verifying {zip_path.name} ===")
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        manifest_member = f"axion/{RELEASE_MANIFEST_ARCNAME}"
        manifest_text = (
            zf.read(manifest_member).decode("utf-8")
            if manifest_member in names else ""
        )
    # Must contain at least these — incl. the runtime modules every
    # Phase 2–16 surface depends on, plus the Phase 17 release manifest.
    must_have = [
        "axion/src/main.py",
        "axion/src/version.py",
        "axion/dashboard/index.html",
        "axion/config/sources.yaml",
        "axion/config/relationships.yaml",
        "axion/requirements.txt",
        "axion/scripts/smoke_local.py",
        "axion/scripts/migrate.py",
        "axion/scripts/support_bundle.py",
        "axion/scripts/rotate_logs.py",
        "axion/src/intelligence/insights/generator.py",
        "axion/src/corporate_events/manual_import.py",
        "axion/src/intelligence/revenue_geography/__init__.py",
        "axion/README_LOCAL.md",
        f"axion/{RELEASE_MANIFEST_ARCNAME}",
    ]
    ok = True
    for needle in must_have:
        if needle not in names:
            print(f"  MISSING: {needle}")
            ok = False
    # Must NOT contain
    must_not = ["Axion/", "Axion-Installers/", ".venv/", "__pycache__/", ".git/", ".env"]
    for bad in must_not:
        leaked = [n for n in names if bad in n and not n.endswith(".env.template")]
        if leaked:
            print(f"  LEAKED ({bad}): {leaked[:3]}{'...' if len(leaked) > 3 else ''}")
            ok = False
    # No database files of any kind.
    db_leaked = [n for n in names if n.endswith((".db", ".db-wal", ".db-shm"))]
    if db_leaked:
        print(f"  LEAKED (database files): {db_leaked[:3]}")
        ok = False
    # Manifest must be present and parse cleanly.
    if not manifest_text:
        print(f"  MISSING: {RELEASE_MANIFEST_ARCNAME}")
        ok = False
    else:
        try:
            man = json.loads(manifest_text)
            for key in ("app_name", "app_version", "release_channel",
                        "platform_package", "build_timestamp", "guarantees"):
                if key not in man:
                    print(f"  MANIFEST missing key: {key}")
                    ok = False
        except json.JSONDecodeError as exc:
            print(f"  MANIFEST not valid JSON: {exc}")
            ok = False
    if ok:
        print("  OK (required paths + manifest present, no excluded paths leaked)")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Axion release zips.")
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "dist"),
        help="Output directory (default: ./dist)",
    )
    parser.add_argument(
        "--platform",
        choices=["windows", "macos", "all"],
        default="all",
        help="Which platform to build (default: all)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output).expanduser().resolve()

    platforms = ["windows", "macos"] if args.platform == "all" else [args.platform]

    built = []
    for p in platforms:
        zip_path = build_zip(p, output_dir)
        built.append(zip_path)

    all_ok = True
    for zip_path in built:
        if not verify_zip(zip_path):
            all_ok = False

    print()
    print("=" * 60)
    if all_ok:
        print("All zips built and verified successfully.")
        print()
        for zip_path in built:
            print(f"  {zip_path}")
        return 0
    print("One or more zips failed verification. See above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
