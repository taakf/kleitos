#!/usr/bin/env python3
"""
Axion by 4Labs — Delivery Package Builder

Generates a clean client-facing delivery folder from the development repository.
Copies only the files needed for production use, excluding internal dev artifacts.

Usage:
    python scripts/prepare-delivery.py                    # Default: ./Axion-Delivery/
    python scripts/prepare-delivery.py --output ~/Desktop  # Custom output location
    python scripts/prepare-delivery.py --no-docs           # Skip optional docs/
    python scripts/prepare-delivery.py --no-macos          # Skip macOS-specific files
    python scripts/prepare-delivery.py --no-docker         # Skip Docker files
"""

import argparse
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── What to include ──────────────────────────────────────────────────────────

REQUIRED_DIRS = [
    "src",
    "config",
    "dashboard",
]

REQUIRED_FILES = [
    "requirements.txt",
    ".env.template",
    "sample_portfolio.csv",
    # Docs
    "README.md",
    "INSTALL.md",
    "OPERATOR_CHECKLIST.md",
    "KNOWN_LIMITATIONS.md",
    "RELEASE_NOTES_V1.md",
    "START_HERE.txt",
]

WINDOWS_FILES = [
    "Axion.bat",
]

MACOS_FILES = [
    "install.sh",
    "start.sh",
    "stop.sh",
    "update.sh",
    "healthcheck.sh",
    "status.sh",
]

MACOS_DIRS = [
    "Axion.app",
]

DOCKER_FILES = [
    "Dockerfile",
    "docker-compose.yml",
]

OPTIONAL_DOCS_DIR = "docs"

# Scripts to include (by name, from scripts/ directory)
SCRIPTS_INCLUDE = [
    "axion-app.pyw",
    "axion-tray.pyw",
    "axion-menubar.py",
    "stop-axion.bat",
    "install-mac.sh",
    "uninstall-mac.sh",
    "deploy-mac.sh",
    "backup.sh",
    "restore.sh",
]

# ── What to exclude from copied directories ──────────────────────────────────

DIR_EXCLUDES = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    ".venv",
    "node_modules",
    "kleitos-data",
    ".claude",
}

FILE_EXCLUDES = {
    ".pyc",
    ".pyo",
    ".DS_Store",
    "Thumbs.db",
}

CONFIG_LAUNCHD_EXCLUDES = {
    "com.kleitos.core.plist",
    "com.kleitos.openclaw.plist",
}


def copytree_filtered(src: Path, dst: Path):
    """Copy a directory tree, excluding dev artifacts."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.iterdir()):
        if item.name in DIR_EXCLUDES:
            continue
        if item.suffix in FILE_EXCLUDES:
            continue
        target = dst / item.name
        if item.is_dir():
            # Special handling for config/launchd — exclude legacy plists
            if item.name == "launchd" and src.name == "config":
                target.mkdir(parents=True, exist_ok=True)
                for plist in item.iterdir():
                    if plist.name not in CONFIG_LAUNCHD_EXCLUDES:
                        shutil.copy2(plist, target / plist.name)
                continue
            copytree_filtered(item, target)
        else:
            shutil.copy2(item, target)


def build_package(output_dir: Path, include_docs: bool, include_macos: bool,
                  include_docker: bool):
    """Build the delivery package."""
    pkg = output_dir / "Axion"

    if pkg.exists():
        print(f"  Removing existing {pkg}...")
        shutil.rmtree(pkg)

    pkg.mkdir(parents=True)
    copied = []

    # 1. Required directories
    for d in REQUIRED_DIRS:
        src = PROJECT_ROOT / d
        if src.is_dir():
            copytree_filtered(src, pkg / d)
            copied.append(f"  {d}/")
        else:
            print(f"  WARNING: Required directory missing: {d}")

    # 2. Required files
    for f in REQUIRED_FILES:
        src = PROJECT_ROOT / f
        if src.is_file():
            shutil.copy2(src, pkg / f)
            copied.append(f"  {f}")
        else:
            print(f"  WARNING: Required file missing: {f}")

    # 3. Windows launcher
    for f in WINDOWS_FILES:
        src = PROJECT_ROOT / f
        if src.is_file():
            shutil.copy2(src, pkg / f)
            copied.append(f"  {f}")

    # 4. macOS files
    if include_macos:
        for f in MACOS_FILES:
            src = PROJECT_ROOT / f
            if src.is_file():
                shutil.copy2(src, pkg / f)
                copied.append(f"  {f}")
        for d in MACOS_DIRS:
            src = PROJECT_ROOT / d
            if src.is_dir():
                copytree_filtered(src, pkg / d)
                copied.append(f"  {d}/")

    # 5. Docker files
    if include_docker:
        for f in DOCKER_FILES:
            src = PROJECT_ROOT / f
            if src.is_file():
                shutil.copy2(src, pkg / f)
                copied.append(f"  {f}")

    # 6. Scripts (filtered)
    scripts_src = PROJECT_ROOT / "scripts"
    scripts_dst = pkg / "scripts"
    scripts_dst.mkdir(exist_ok=True)
    for name in SCRIPTS_INCLUDE:
        src = scripts_src / name
        if src.is_file():
            shutil.copy2(src, scripts_dst / name)
            copied.append(f"  scripts/{name}")

    # 7. Optional docs
    if include_docs:
        docs_src = PROJECT_ROOT / OPTIONAL_DOCS_DIR
        if docs_src.is_dir():
            copytree_filtered(docs_src, pkg / OPTIONAL_DOCS_DIR)
            copied.append(f"  {OPTIONAL_DOCS_DIR}/")

    return pkg, copied


def verify_package(pkg: Path):
    """Verify the package contents."""
    issues = []

    # Must exist
    must_exist = [
        "Axion.bat", "src/main.py", "src/config.py",
        "config/settings.yaml", "config/sources.yaml",
        "dashboard/index.html", "dashboard/js/app.js", "dashboard/css/styles.css",
        "requirements.txt", ".env.template", "sample_portfolio.csv",
        "README.md", "INSTALL.md", "OPERATOR_CHECKLIST.md",
        "START_HERE.txt", "scripts/axion-tray.pyw",
    ]
    for f in must_exist:
        if not (pkg / f).exists():
            issues.append(f"MISSING: {f}")

    # Must NOT exist
    must_not_exist = [
        "ARCHITECTURE.md", "RELEASE_HARDENING_LOG.md", "RELEASE_BACKLOG.md",
        "RELEASE_DECISIONS.md", "RELEASE_READINESS_CHECKLIST.md",
        "DELIVERY_GUIDE.md", "Kleitos.bat", "pyproject.toml",
        "test_api.py", "test_full_pipeline.py", "tests",
        ".venv", "kleitos-data", "__pycache__", ".git", ".claude",
        "scripts/kleitos-tray.pyw", "scripts/kleitos-menubar.py",
        "scripts/stop-kleitos.bat", "scripts/build-exe.py",
        "scripts/generate-icons.py",
        "config/launchd/com.kleitos.core.plist",
        "config/launchd/com.kleitos.openclaw.plist",
        "Kleitos.app",
    ]
    for f in must_not_exist:
        if (pkg / f).exists():
            issues.append(f"SHOULD NOT EXIST: {f}")

    # Check no .env with secrets
    env_file = pkg / ".env"
    if env_file.exists():
        issues.append("SECURITY: .env file present (may contain secrets)")

    return issues


def main():
    parser = argparse.ArgumentParser(
        description="Axion by 4Labs — Build a clean delivery package"
    )
    parser.add_argument(
        "--output", "-o", type=str, default=str(PROJECT_ROOT),
        help="Output directory (default: project root, creates Axion/ inside it)"
    )
    parser.add_argument("--no-docs", action="store_true", help="Skip optional docs/ folder")
    parser.add_argument("--no-macos", action="store_true", help="Skip macOS-specific files")
    parser.add_argument("--no-docker", action="store_true", help="Skip Docker files")
    args = parser.parse_args()

    output_dir = Path(args.output).resolve()
    print()
    print("  ============================================")
    print("   Axion by 4Labs -- Delivery Packager")
    print("  ============================================")
    print()
    print(f"  Source:  {PROJECT_ROOT}")
    print(f"  Output:  {output_dir / 'Axion'}")
    print(f"  Docs:    {'yes' if not args.no_docs else 'no'}")
    print(f"  macOS:   {'yes' if not args.no_macos else 'no'}")
    print(f"  Docker:  {'yes' if not args.no_docker else 'no'}")
    print()

    print("  Building package...")
    pkg, copied = build_package(
        output_dir,
        include_docs=not args.no_docs,
        include_macos=not args.no_macos,
        include_docker=not args.no_docker,
    )

    print(f"  Copied {len(copied)} items:")
    for c in copied:
        print(f"    {c}")
    print()

    print("  Verifying package...")
    issues = verify_package(pkg)
    if issues:
        print(f"  WARNING: {len(issues)} issue(s) found:")
        for issue in issues:
            print(f"    - {issue}")
    else:
        print("  OK: Package verified -- all required files present, no unwanted files.")

    # Count files
    file_count = sum(1 for _ in pkg.rglob("*") if _.is_file())
    dir_size_mb = sum(f.stat().st_size for f in pkg.rglob("*") if f.is_file()) / (1024 * 1024)

    print()
    print(f"  Package ready: {pkg}")
    print(f"  Files: {file_count}  Size: {dir_size_mb:.1f} MB")
    print()

    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
