"""
Build Axion.exe — the native desktop app for Axion by 4Labs.

This bundles scripts/axion-app.pyw into a single .exe with:
  - Native window shell via pywebview (Edge WebView2 on Windows)
  - Custom Axion icon
  - No console window (windowed mode)
  - Branded splash screen during startup

Usage:
    python scripts/build-exe.py

Output:
    dist/Axion.exe

Requirements:
    pip install pyinstaller pywebview
"""

import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_DIR / "scripts"
ASSETS_DIR = PROJECT_DIR / "assets"
ICON_FILE = ASSETS_DIR / "kleitos.ico"
APP_SCRIPT = SCRIPTS_DIR / "axion-app.pyw"


def ensure_deps():
    """Install build dependencies if missing."""
    deps = ["pyinstaller", "pywebview", "pystray", "Pillow"]
    for dep in deps:
        try:
            __import__(dep.lower().replace("-", "_"))
        except ImportError:
            if dep == "Pillow":
                try:
                    __import__("PIL")
                    continue
                except ImportError:
                    pass
            if dep == "pyinstaller":
                try:
                    __import__("PyInstaller")
                    continue
                except ImportError:
                    pass
            print(f"  Installing {dep}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", dep, "-q"])


def generate_icons():
    """Generate icons if they don't exist."""
    if not ICON_FILE.exists():
        print("  Generating icons...")
        subprocess.check_call(
            [sys.executable, str(SCRIPTS_DIR / "generate-icons.py")],
            cwd=str(PROJECT_DIR),
        )


def build_exe():
    """Run PyInstaller to create Axion.exe."""
    print("  Building Axion.exe with PyInstaller...")

    # Collect tray icon assets to bundle
    add_data = []
    for png in ASSETS_DIR.glob("tray-*.png"):
        # PyInstaller --add-data format: "source;destination" on Windows
        add_data.extend(["--add-data", f"{png};assets"])
    # Also include the main icon
    if (ASSETS_DIR / "kleitos-256.png").exists():
        add_data.extend(["--add-data", f"{ASSETS_DIR / 'kleitos-256.png'};assets"])

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",               # No console window
        "--name", "Axion",
        "--icon", str(ICON_FILE),
        "--distpath", str(PROJECT_DIR / "dist"),
        "--workpath", str(PROJECT_DIR / "build"),
        "--specpath", str(PROJECT_DIR),
        # Clean build
        "--clean",
        # Hidden imports for pywebview
        "--hidden-import", "webview",
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "clr_loader",
        "--hidden-import", "pythonnet",
        # Hidden imports for embedded tray companion
        "--hidden-import", "pystray",
        "--hidden-import", "pystray._win32",
        "--hidden-import", "PIL._tkinter_finder",
        # Hidden imports for PDF extraction (pdfplumber)
        "--hidden-import", "pdfplumber",
        "--hidden-import", "pdfminer",
        "--hidden-import", "pdfminer.high_level",
        "--hidden-import", "pdfminer.layout",
        # Hidden imports for OpenAI provider
        "--hidden-import", "openai",
        "--hidden-import", "openai.resources",
        "--hidden-import", "openai._client",
        # Hidden imports for Google Gemini provider
        "--hidden-import", "google.generativeai",
        "--hidden-import", "google.ai.generativelanguage",
    ] + add_data + [
        str(APP_SCRIPT),
    ]

    print(f"  Command: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(PROJECT_DIR))

    if result.returncode != 0:
        print("\n  ERROR: PyInstaller build failed!")
        sys.exit(1)

    exe_path = PROJECT_DIR / "dist" / "Axion.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"\n  Build successful!")
        print(f"  Output: {exe_path}")
        print(f"  Size:   {size_mb:.1f} MB")
        print()
        print(f"  To test: double-click dist\\Axion.exe")
        print(f"  To distribute: copy dist\\Axion.exe + the project folder")
    else:
        print("\n  ERROR: Expected output not found!")
        sys.exit(1)


def main():
    print("=" * 50)
    print("  Axion by 4Labs — Windows .exe Builder")
    print("=" * 50)
    print()

    print("[1/3] Checking dependencies...")
    ensure_deps()

    print("[2/3] Checking icons...")
    generate_icons()

    print("[3/3] Building executable...")
    build_exe()


if __name__ == "__main__":
    main()
