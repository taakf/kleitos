"""
Axion Desktop App — native window shell for Axion by 4Labs.

Opens a native OS window (Edge WebView2 on Windows, WebKit on macOS)
that hosts the Axion dashboard. The local server is started automatically
and stopped when the window is closed.

Usage:
    pythonw scripts/axion-app.pyw          # Normal launch
    pythonw scripts/axion-app.pyw --dev    # Dev mode (server already running)
"""

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_FROZEN = getattr(sys, "frozen", False)

if _FROZEN:
    _EXE_DIR = Path(sys.executable).resolve().parent
    PROJECT_DIR = _EXE_DIR.parent if (_EXE_DIR.parent / "src").exists() else _EXE_DIR
else:
    SCRIPT_DIR = Path(__file__).resolve().parent
    PROJECT_DIR = SCRIPT_DIR.parent

VENV_DIR = PROJECT_DIR / ".venv"
DATA_DIR = Path.home() / "kleitos-data"
LOG_DIR = DATA_DIR / "logs"
PID_FILE = DATA_DIR / "kleitos.pid"

PORT = int(os.environ.get("KLEITOS_PORT", 7777))
BASE_URL = f"http://localhost:{PORT}"
HEALTH_URL = f"{BASE_URL}/api/v1/health"
DASHBOARD_URL = f"{BASE_URL}/dashboard/"

for d in [DATA_DIR / "db", LOG_DIR, DATA_DIR / "backups"]:
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_DIR / "app.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("axion-app")
logging.getLogger("urllib3").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Single-instance guard (Windows named mutex)
# ---------------------------------------------------------------------------
_mutex_handle = None

def _acquire_single_instance():
    """Returns True if this is the only instance."""
    if sys.platform != "win32":
        return True
    global _mutex_handle
    import ctypes
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\AxionDesktopApp")
    last_error = ctypes.windll.kernel32.GetLastError()
    if last_error == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None
        return False
    return True


# ---------------------------------------------------------------------------
# Python / venv resolution — NEVER returns the frozen exe
# ---------------------------------------------------------------------------
def _get_venv_python():
    """Get the venv Python path, or None if venv is not set up."""
    for subpath in ["Scripts/python.exe", "bin/python"]:
        p = VENV_DIR / subpath
        if p.exists():
            return str(p)
    return None


def _find_system_python():
    """Find a system Python 3.11+ for venv creation."""
    import re
    for cmd in ["python3.12", "python3.11", "python3", "python", "py -3"]:
        try:
            parts = cmd.split()
            result = subprocess.run(
                parts + ["--version"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                ver_text = result.stdout.strip() + result.stderr.strip()
                m = re.search(r"(\d+)\.(\d+)", ver_text)
                if m and int(m.group(1)) >= 3 and int(m.group(2)) >= 11:
                    log.info(f"System Python: {ver_text.strip()}")
                    return parts
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# First-time setup
# ---------------------------------------------------------------------------
def _needs_setup():
    """Check if venv + dependencies need to be installed."""
    python = _get_venv_python()
    if not python:
        return True
    try:
        result = subprocess.run(
            [python, "-c", "import fastapi, uvicorn"],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode != 0
    except Exception:
        return True


def _run_setup(window=None):
    """Create venv and install dependencies. Updates splash if window available."""
    def _set_status(msg):
        log.info(f"Setup: {msg}")
        if window:
            try:
                window.evaluate_js(
                    f"document.getElementById('status-text').textContent = '{msg}';"
                )
            except Exception:
                pass

    _set_status("Setting up (first launch)...")

    # Remove broken venv
    if VENV_DIR.exists():
        log.info("Removing broken venv")
        shutil.rmtree(VENV_DIR, ignore_errors=True)

    sys_python = _find_system_python()
    if not sys_python:
        _set_status("Python 3.11+ not found. Please install from python.org")
        log.error("System Python not found")
        return False

    # Create venv
    _set_status("Creating virtual environment...")
    result = subprocess.run(
        sys_python + ["-m", "venv", str(VENV_DIR)],
        capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        log.error(f"venv creation failed: {result.stderr}")
        _set_status("Setup failed. Check logs.")
        return False

    venv_python = _get_venv_python()
    if not venv_python:
        log.error("venv created but python not found")
        _set_status("Setup failed. Check logs.")
        return False

    # Upgrade pip
    subprocess.run(
        [venv_python, "-m", "pip", "install", "--upgrade", "pip", "-q"],
        capture_output=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    # Install deps with progress
    _set_status("Installing dependencies (2-5 min)...")
    req_file = PROJECT_DIR / "requirements.txt"
    setup_log = LOG_DIR / "setup-install.log"

    try:
        pip_proc = subprocess.Popen(
            [venv_python, "-m", "pip", "install", "-r", str(req_file)],
            stdout=open(str(setup_log), "w", encoding="utf-8", errors="replace"),
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        elapsed = 0
        while pip_proc.poll() is None and elapsed < 600:
            time.sleep(5)
            elapsed += 5
            if elapsed % 30 == 0:
                minutes = elapsed // 60
                seconds = elapsed % 60
                _set_status(f"Installing... ({minutes}m {seconds}s)")

        if pip_proc.poll() is None:
            pip_proc.kill()
            log.error("pip install timed out")
            _set_status("Install timed out. Check logs.")
            return False

        if pip_proc.returncode != 0:
            log.error(f"pip install failed (exit {pip_proc.returncode})")
            _set_status("Install failed. Check logs.")
            return False

    except Exception as e:
        log.error(f"pip error: {e}")
        _set_status("Install error. Check logs.")
        return False

    # Verify
    _set_status("Verifying installation...")
    result = subprocess.run(
        [venv_python, "-c", "import fastapi, uvicorn, sqlalchemy"],
        capture_output=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        log.error("Package verification failed")
        _set_status("Verification failed. Check logs.")
        return False

    # Create .env if needed
    env_file = PROJECT_DIR / ".env"
    if not env_file.exists():
        env_file.write_text(
            "# Axion Environment Configuration\n"
            "# Anthropic API key (optional)\n"
            "# ANTHROPIC_API_KEY=sk-ant-...\n",
            encoding="utf-8",
        )

    log.info(f"Setup complete in {elapsed}s")
    _set_status("Setup complete! Starting server...")
    return True


# ---------------------------------------------------------------------------
# Server management
# ---------------------------------------------------------------------------
_server_proc = None


def _is_server_running():
    try:
        import urllib.request
        r = urllib.request.urlopen(HEALTH_URL, timeout=3)
        return r.status == 200
    except Exception:
        return False


def _start_server():
    """Start the uvicorn server as a subprocess."""
    global _server_proc
    if _is_server_running():
        log.info("Server already running")
        return True

    python = _get_venv_python()
    if not python:
        log.error("Cannot start server: no venv Python found")
        return False

    env = os.environ.copy()
    env["KLEITOS_DATA_DIR"] = str(DATA_DIR)
    env["KLEITOS_DB_PATH"] = str(DATA_DIR / "db" / "kleitos.db")
    env["PATH"] = f"{VENV_DIR / 'Scripts'};{env.get('PATH', '')}"

    stdout_fh = open(LOG_DIR / "kleitos-stdout.log", "a")
    stderr_fh = open(LOG_DIR / "kleitos-stderr.log", "a")

    _server_proc = subprocess.Popen(
        [python, "-m", "uvicorn", "src.main:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(PROJECT_DIR),
        env=env,
        stdout=stdout_fh,
        stderr=stderr_fh,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    PID_FILE.write_text(str(_server_proc.pid))
    log.info(f"Server started (PID {_server_proc.pid})")

    # Wait for health
    for i in range(45):
        if _server_proc.poll() is not None:
            log.error("Server died during startup")
            return False
        if _is_server_running():
            log.info(f"Server healthy after {i+1}s")
            return True
        time.sleep(1)

    log.warning("Server started but health check timed out")
    return _is_server_running()


def _stop_server():
    """Stop the server subprocess."""
    global _server_proc
    if _server_proc and _server_proc.poll() is None:
        _server_proc.terminate()
        try:
            _server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _server_proc.kill()
        log.info("Server stopped")
    PID_FILE.unlink(missing_ok=True)
    _server_proc = None


# ---------------------------------------------------------------------------
# Splash / Loading HTML
# ---------------------------------------------------------------------------
SPLASH_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0d0f14;
    color: #e8e9ed;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100vh;
    overflow: hidden;
  }
  .splash {
    text-align: center;
    animation: fadeIn 0.6s ease;
  }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: none; } }
  .logo {
    font-size: 42px;
    font-weight: 700;
    letter-spacing: 3px;
    margin-bottom: 6px;
    background: linear-gradient(135deg, #60a5fa, #818cf8);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .maker {
    font-size: 13px;
    color: #6b7280;
    letter-spacing: 1px;
    margin-bottom: 40px;
  }
  .spinner-wrap {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    margin-bottom: 16px;
  }
  .spinner {
    width: 20px; height: 20px;
    border: 2px solid #1e2130;
    border-top-color: #60a5fa;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .status {
    font-size: 14px;
    color: #9ca3af;
  }
  #status-text { transition: opacity 0.3s; }
</style>
</head>
<body>
<div class="splash">
  <div class="logo">AXION</div>
  <div class="maker">by 4Labs</div>
  <div class="spinner-wrap">
    <div class="spinner"></div>
    <span class="status" id="status-text">Starting...</span>
  </div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Windows toast notification (best-effort)
# ---------------------------------------------------------------------------
def _notify(message):
    """Show a Windows toast notification. Fails silently."""
    if sys.platform != "win32":
        return
    try:
        ps_script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType = WindowsRuntime] > $null; "
            "$t = [Windows.UI.Notifications.ToastNotificationManager]::"
            "GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
            "$n = $t.GetElementsByTagName('text'); "
            f"$n.Item(0).AppendChild($t.CreateTextNode('Axion')) > $null; "
            f"$n.Item(1).AppendChild($t.CreateTextNode('{message}')) > $null; "
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($t); "
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Axion')"
            ".Show($toast)"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", ps_script],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Inter-process "show window" signal
# ---------------------------------------------------------------------------
_SHOW_SIGNAL = DATA_DIR / ".show-window"
_QUIT_SIGNAL = DATA_DIR / ".quit-app"


def _request_show():
    """Second instance writes this to ask the running instance to show."""
    _SHOW_SIGNAL.write_text("show", encoding="utf-8")


# ---------------------------------------------------------------------------
# Main app window
# ---------------------------------------------------------------------------
def _run_app(dev_mode=False):
    try:
        import webview
    except ImportError:
        log.warning("pywebview not available — falling back to browser")
        _fallback_browser(dev_mode)
        return

    log.info("Starting Axion desktop app")

    # Clean stale signals
    _SHOW_SIGNAL.unlink(missing_ok=True)
    _QUIT_SIGNAL.unlink(missing_ok=True)

    _app_state = {"quitting": False, "hidden": False, "hide_notified": False}

    # Create window with splash
    window = webview.create_window(
        "Axion — Portfolio Intelligence",
        html=SPLASH_HTML,
        width=1280,
        height=820,
        min_size=(1024, 680),
        background_color="#0d0f14",
        text_select=True,
    )

    def _on_loaded():
        """After window is shown, handle setup + start + navigate."""
        try:
            # First-time setup if needed
            if not dev_mode and _needs_setup():
                log.info("First-time setup required")
                if not _run_setup(window):
                    log.error("Setup failed — staying on splash with error")
                    return

            # Start server
            if not dev_mode:
                log.info("Starting server from app shell...")
                window.evaluate_js(
                    "document.getElementById('status-text').textContent = "
                    "'Starting server...';"
                )
                success = _start_server()
                if not success:
                    window.evaluate_js(
                        "document.getElementById('status-text').textContent = "
                        "'Server failed to start. Check logs.';"
                    )
                    log.error("Server failed to start")
                    return
            else:
                # Dev mode — wait for server
                if not _is_server_running():
                    window.evaluate_js(
                        "document.getElementById('status-text').textContent = "
                        "'Waiting for server...';"
                    )
                    for _ in range(30):
                        if _is_server_running():
                            break
                        time.sleep(1)

            # Navigate to dashboard
            log.info("Navigating to dashboard")
            window.load_url(DASHBOARD_URL)

        except Exception as e:
            log.error(f"Startup error: {e}", exc_info=True)

    def _on_closing():
        """Close button: hide window instead of quitting (server keeps running).
        Return False to cancel the close.  Return nothing to allow real quit."""
        if _app_state["quitting"]:
            # Real quit requested — allow close and stop server
            log.info("Quitting Axion — stopping server and tray")
            if not dev_mode:
                _stop_server()
            _stop_tray()
            _SHOW_SIGNAL.unlink(missing_ok=True)
            _QUIT_SIGNAL.unlink(missing_ok=True)
            return  # Allow the close

        # Hide instead of close — server keeps running
        log.info("Window hidden — Axion still running in background")
        _app_state["hidden"] = True
        window.hide()

        # One-time notification so user understands the behavior
        if not _app_state["hide_notified"]:
            _app_state["hide_notified"] = True
            _notify("Axion is still running. Launch again to reopen, or quit from Settings.")

        return False  # Cancel the close

    window.events.closing += _on_closing

    def _signal_watcher():
        """Watch for inter-process signals to show or quit."""
        while not _app_state["quitting"]:
            try:
                if _SHOW_SIGNAL.exists():
                    _SHOW_SIGNAL.unlink(missing_ok=True)
                    log.info("Show signal received — restoring window")
                    _app_state["hidden"] = False
                    window.show()
                if _QUIT_SIGNAL.exists():
                    _QUIT_SIGNAL.unlink(missing_ok=True)
                    log.info("Quit signal received")
                    _app_state["quitting"] = True
                    _stop_tray()
                    window.destroy()
            except Exception:
                pass
            time.sleep(1)

    # -------------------------------------------------------------------
    # Embedded tray companion (Windows only)
    # -------------------------------------------------------------------
    _tray_icon = None

    def _start_tray():
        """Create a lightweight system tray icon as a companion to the window."""
        if sys.platform != "win32":
            return
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            log.info("pystray/Pillow not available — tray companion skipped")
            return

        def _make_icon(color=(34, 197, 94)):
            """Generate a small branded tray icon."""
            size = 64
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle([(2, 2), (62, 62)], radius=12, fill=color)
            try:
                from PIL import ImageFont
                font = ImageFont.truetype("segoeui.ttf", 36)
            except Exception:
                font = ImageFont.load_default()
            draw.text((16, 10), "A", fill="white", font=font)
            return img

        def _on_open(icon, item):
            log.info("Tray: Open Axion")
            _app_state["hidden"] = False
            try:
                window.show()
            except Exception:
                pass

        def _on_quit(icon, item):
            log.info("Tray: Quit Axion")
            _app_state["quitting"] = True
            icon.stop()
            try:
                window.destroy()
            except Exception:
                pass

        nonlocal _tray_icon
        _tray_icon = pystray.Icon(
            "axion",
            _make_icon(),
            "Axion — Running",
            menu=pystray.Menu(
                pystray.MenuItem("Open Axion", _on_open, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit Axion", _on_quit),
            ),
        )
        log.info("Tray companion started")
        _tray_icon.run()  # Blocks this thread

    def _stop_tray():
        """Stop the tray icon if it exists."""
        nonlocal _tray_icon
        if _tray_icon:
            try:
                _tray_icon.stop()
            except Exception:
                pass
            _tray_icon = None

    # Start the server connection in background after window appears
    def _setup(window_ref):
        threading.Thread(target=_on_loaded, daemon=True).start()
        threading.Thread(target=_signal_watcher, daemon=True).start()
        threading.Thread(target=_start_tray, daemon=True).start()

    webview.start(func=_setup, args=[window], gui="edgechromium")

    # After webview.start() returns (window destroyed), clean up tray
    _stop_tray()


def _fallback_browser(dev_mode=False):
    """Fallback: start server and open in browser."""
    import webbrowser
    if not dev_mode:
        if _needs_setup():
            _run_setup()
        _start_server()
    webbrowser.open(DASHBOARD_URL)
    log.info("Opened dashboard in browser (fallback mode)")
    try:
        while _server_proc and _server_proc.poll() is None:
            time.sleep(5)
    except KeyboardInterrupt:
        _stop_server()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Single-instance guard
    if not _acquire_single_instance():
        # Another instance is running — ask it to show its window
        log.info("Another instance running — sending show signal")
        _request_show()
        # Brief wait, then fallback to browser if window didn't appear
        time.sleep(2)
        if _is_server_running():
            import webbrowser
            webbrowser.open(DASHBOARD_URL)
        sys.exit(0)

    dev_mode = "--dev" in sys.argv
    try:
        _run_app(dev_mode)
    except Exception as e:
        log.error(f"App failed: {e}", exc_info=True)
        _fallback_browser(dev_mode)
