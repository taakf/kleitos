"""
Axion System Tray App — the premium Windows launcher.

Double-click Axion.exe (or run this script with pythonw) and Axion
appears in the system tray. The server starts automatically, and you get:

  - Branded tray icon with live status indicator (green/amber/gray)
  - Open Dashboard (default action on double-click)
  - Start / Stop / Restart server
  - View Logs / Open Data Folder
  - First-launch auto-setup (venv + dependencies)
  - Auto-restart on crash with exponential backoff
  - Single-instance guard (only one tray app at a time)
  - Quit (stops server and exits)
"""

import ctypes
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

# ---------------------------------------------------------------------------
# Single-instance guard (Windows named mutex)
# ---------------------------------------------------------------------------
_MUTEX_NAME = "Global\\AxionTrayApp"
_mutex_handle = None


def _acquire_single_instance():
    """Returns True if this is the only instance, False if another is running."""
    global _mutex_handle
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    last_error = ctypes.windll.kernel32.GetLastError()
    if last_error == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None
        return False
    return True


def _is_server_healthy():
    """Quick check whether the Axion server is actually responding."""
    try:
        import urllib.request
        r = urllib.request.urlopen(HEALTH_URL, timeout=3)
        return r.status == 200
    except Exception:
        return False


def _kill_stale_tray():
    """Try to terminate a stale tray/server process using PID file or port scan."""
    # 1. Try PID file
    try:
        if PID_FILE.exists():
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 9)  # SIGKILL
    except (ValueError, OSError, ProcessLookupError):
        pass
    PID_FILE.unlink(missing_ok=True)

    # 2. Kill any python/uvicorn listening on our port
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in result.stdout.splitlines():
            if f":{PORT} " in line and "LISTENING" in line:
                parts = line.split()
                pid = int(parts[-1])
                try:
                    os.kill(pid, 9)
                except (OSError, ProcessLookupError):
                    pass
    except Exception:
        pass

    # 3. Brief pause to let OS release resources
    time.sleep(1)


# ---------------------------------------------------------------------------
# Ensure dependencies (pystray, Pillow, requests)
# ---------------------------------------------------------------------------
_TRAY_DEPS = ["pystray", "Pillow", "requests"]


def _ensure_deps():
    missing = []
    for pkg in _TRAY_DEPS:
        try:
            __import__(pkg.lower().replace("-", "_"))
        except ImportError:
            if pkg == "Pillow":
                try:
                    __import__("PIL")
                    continue
                except ImportError:
                    pass
            missing.append(pkg)
    if missing:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install"] + missing + ["-q"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


_ensure_deps()

import pystray  # noqa: E402
from PIL import Image  # noqa: E402

try:
    import requests  # noqa: E402
except ImportError:
    import urllib.request

    class _Requests:
        class ConnectionError(Exception):
            pass

        @staticmethod
        def get(url, timeout=5):
            class _Resp:
                def __init__(self, code):
                    self.status_code = code
            try:
                with urllib.request.urlopen(url, timeout=timeout) as r:
                    return _Resp(r.status)
            except Exception:
                raise _Requests.ConnectionError()

    requests = _Requests()

# ---------------------------------------------------------------------------
# Constants (port is configurable via KLEITOS_PORT env var)
# ---------------------------------------------------------------------------
PORT = int(os.environ.get("KLEITOS_PORT", 7777))
HEALTH_URL = f"http://localhost:{PORT}/api/v1/health"
DASHBOARD_URL = f"http://localhost:{PORT}"

# When running as a PyInstaller .exe, __file__ is inside a temp dir.
if getattr(sys, "frozen", False):
    _BUNDLE_DIR = Path(sys._MEIPASS)
    _EXE_DIR = Path(sys.executable).resolve().parent
    PROJECT_DIR = _EXE_DIR.parent if (_EXE_DIR.parent / "src").exists() else _EXE_DIR
    ASSETS_DIR = _BUNDLE_DIR / "assets"
else:
    SCRIPT_DIR = Path(__file__).resolve().parent
    PROJECT_DIR = SCRIPT_DIR.parent
    ASSETS_DIR = PROJECT_DIR / "assets"

VENV_DIR = PROJECT_DIR / ".venv"
DATA_DIR = Path.home() / "kleitos-data"
LOG_DIR = DATA_DIR / "logs"
PID_FILE = DATA_DIR / "kleitos.pid"

# Ensure data directories exist
for d in [DATA_DIR / "db", LOG_DIR, DATA_DIR / "backups"]:
    d.mkdir(parents=True, exist_ok=True)

# Set up logging
logging.basicConfig(
    filename=str(LOG_DIR / "tray.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("axion-tray")

# Suppress noisy HTTP connection debug logs from urllib3/requests
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Log rotation
# ---------------------------------------------------------------------------
def _rotate_log(path, max_size_mb=10, max_backups=3):
    """Rotate a log file if it exceeds max_size_mb."""
    path = Path(path)
    if not path.exists():
        return
    try:
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb < max_size_mb:
            return
        # Shift existing backups
        for i in range(max_backups, 0, -1):
            src = path.with_suffix(f"{path.suffix}.{i}")
            dst = path.with_suffix(f"{path.suffix}.{i + 1}")
            if i == max_backups and src.exists():
                src.unlink()
            elif src.exists():
                src.rename(dst)
        # Rotate current to .1
        path.rename(path.with_suffix(f"{path.suffix}.1"))
        log.info(f"Rotated log: {path.name}")
    except Exception as e:
        log.debug(f"Log rotation failed for {path}: {e}")


# ---------------------------------------------------------------------------
# Icon loading
# ---------------------------------------------------------------------------
_icon_cache = {}


def load_tray_icon(status="stopped"):
    """Load pre-generated tray icon from assets/, with fallback to generated."""
    if status in _icon_cache:
        return _icon_cache[status]

    icon_path = ASSETS_DIR / f"tray-{status}.png"
    if icon_path.exists():
        try:
            img = Image.open(icon_path)
            _icon_cache[status] = img
            return img
        except Exception:
            pass  # Fall through to generated icon

    # Fallback: generate a simple icon
    from PIL import ImageDraw
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    colors = {
        "healthy": (34, 197, 94),
        "unhealthy": (251, 191, 36),
        "stopped": (180, 180, 190),
        "starting": (96, 165, 250),
    }
    bg = colors.get(status, (180, 180, 190))
    draw.rounded_rectangle([(2, 2), (62, 62)], radius=12, fill=bg)
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("segoeui.ttf", 36)
    except Exception:
        from PIL import ImageFont
        font = ImageFont.load_default()
    draw.text((16, 10), "A", fill="white", font=font)
    _icon_cache[status] = img
    return img


# ---------------------------------------------------------------------------
# Windows toast notifications
# ---------------------------------------------------------------------------
def _notify_windows(title, message):
    """Show a Windows 10/11 toast notification via PowerShell."""
    try:
        ps_script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType = WindowsRuntime] > $null; "
            "$template = [Windows.UI.Notifications.ToastNotificationManager]::"
            "GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
            "$textNodes = $template.GetElementsByTagName('text'); "
            f"$textNodes.Item(0).AppendChild($template.CreateTextNode('{title}')) > $null; "
            f"$textNodes.Item(1).AppendChild($template.CreateTextNode('{message}')) > $null; "
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
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


def notify(message):
    _notify_windows("Axion", message)


# ---------------------------------------------------------------------------
# PID validation
# ---------------------------------------------------------------------------
def _is_axion_pid(pid):
    """Check if a PID belongs to an Axion/uvicorn process."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/V"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = result.stdout.lower()
        return "uvicorn" in output or "python" in output
    except Exception:
        return False


# ---------------------------------------------------------------------------
# First-launch auto-setup
# ---------------------------------------------------------------------------
def needs_setup():
    """Check if venv + dependencies need to be installed or repaired."""
    python = VENV_DIR / "Scripts" / "python.exe"
    if not python.exists():
        return True
    # Verify venv is functional
    try:
        result = subprocess.run(
            [str(python), "-c", "import fastapi, uvicorn"],
            capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return result.returncode != 0
    except Exception:
        return True


def run_first_time_setup():
    """Create venv and install dependencies. Returns True on success."""
    log.info("Running first-time setup...")
    notify("First launch — setting up (1-2 minutes)...")

    # Remove broken venv if it exists
    if VENV_DIR.exists():
        log.info("Removing broken venv...")
        shutil.rmtree(VENV_DIR, ignore_errors=True)

    # Find system Python
    sys_python = None
    for cmd in ["python3.12", "python3.11", "python3", "python", "py -3"]:
        try:
            parts = cmd.split()
            result = subprocess.run(
                parts + ["--version"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                import re
                ver_text = result.stdout.strip() + result.stderr.strip()
                m = re.search(r"(\d+)\.(\d+)", ver_text)
                if m and int(m.group(1)) >= 3 and int(m.group(2)) >= 11:
                    sys_python = parts
                    log.info(f"Found: {ver_text.strip()}")
                    break
        except Exception:
            continue

    if not sys_python:
        log.error("Python 3.11+ not found")
        ctypes.windll.user32.MessageBoxW(
            0,
            "Python 3.11+ is required but was not found.\n\n"
            "Please install Python from python.org\n"
            "(check 'Add Python to PATH' during install)\n"
            "then launch Axion again.",
            "Axion — Setup Required",
            0x30,
        )
        webbrowser.open("https://www.python.org/downloads/")
        return False

    # Create venv
    notify("Creating virtual environment...")
    log.info("Creating virtual environment...")
    result = subprocess.run(
        sys_python + ["-m", "venv", str(VENV_DIR)],
        capture_output=True, text=True, timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        log.error(f"venv creation failed: {result.stderr}")
        return False

    venv_python = str(VENV_DIR / "Scripts" / "python.exe")

    # Upgrade pip
    subprocess.run(
        [venv_python, "-m", "pip", "install", "--upgrade", "pip", "-q"],
        capture_output=True, timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    # Install requirements with progress feedback
    notify("Installing dependencies (2-5 min on first launch)...")
    log.info("Installing dependencies...")
    req_file = PROJECT_DIR / "requirements.txt"
    setup_log = LOG_DIR / "setup-install.log"

    try:
        pip_proc = subprocess.Popen(
            [venv_python, "-m", "pip", "install", "-r", str(req_file)],
            stdout=open(str(setup_log), "w", encoding="utf-8", errors="replace"),
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        # Poll with periodic progress notifications
        elapsed = 0
        poll_interval = 5  # seconds
        notify_interval = 45  # seconds between user notifications
        last_notify = 0
        max_wait = 600  # 10 minutes absolute max

        while pip_proc.poll() is None and elapsed < max_wait:
            time.sleep(poll_interval)
            elapsed += poll_interval
            if elapsed - last_notify >= notify_interval:
                minutes = elapsed // 60
                seconds = elapsed % 60
                msg = f"Still installing... ({minutes}m {seconds}s elapsed)"
                notify(msg)
                log.info(msg)
                last_notify = elapsed

        if pip_proc.poll() is None:
            # Timed out — kill it
            pip_proc.kill()
            pip_proc.wait(timeout=5)
            log.error(f"pip install timed out after {max_wait}s")
            notify(f"Install timed out. Check {setup_log}")
            return False

        if pip_proc.returncode != 0:
            log.error(f"pip install failed (exit code {pip_proc.returncode}). See {setup_log}")
            notify(f"Install failed. Check logs: {setup_log}")
            return False

        log.info(f"Dependencies installed successfully in {elapsed}s")

    except Exception as e:
        log.error(f"pip install error: {e}", exc_info=True)
        notify(f"Install error. Check {LOG_DIR / 'tray.log'}")
        return False

    # Verify
    notify("Verifying installation...")
    result = subprocess.run(
        [venv_python, "-c", "import fastapi, uvicorn, sqlalchemy, aiosqlite, apscheduler"],
        capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        log.error("Package verification failed")
        return False

    # Create .env if needed
    env_file = PROJECT_DIR / ".env"
    if not env_file.exists():
        env_file.write_text(
            "# Axion Environment Configuration\n"
            "# -----------------------------------\n"
            "# Anthropic API key (optional - works without it using rule-based fallbacks)\n"
            "# ANTHROPIC_API_KEY=sk-ant-...\n"
            "\n"
            "# NewsAPI key (optional - for news collection)\n"
            "# NEWSAPI_KEY=...\n",
            encoding="utf-8",
        )

    log.info("First-time setup complete")
    notify("Setup complete! Starting Axion...")
    return True


# ---------------------------------------------------------------------------
# AxionTray
# ---------------------------------------------------------------------------
class AxionTray:
    def __init__(self, autostart=True):
        self._lock = threading.Lock()
        self.status = "stopped"
        self.server_proc = None
        self._log_handles = []  # Track open log file handles
        self._running = True
        self._autostart = autostart
        self._first_healthy = False

        # Auto-restart state
        self._restart_count = 0
        self._max_restarts = 5
        self._user_stopped = False  # True if user explicitly stopped

        self.icon = pystray.Icon(
            "axion",
            load_tray_icon("stopped"),
            "Axion",
            menu=self._build_menu("stopped"),
        )
        self.icon.HAS_DEFAULT_ACTION = True

    def _build_menu(self, status):
        status_texts = {
            "healthy": "Status: Running",
            "unhealthy": "Status: Unhealthy",
            "stopped": "Status: Not Running",
            "starting": "Status: Starting...",
            "setup": "Status: Setting up...",
        }
        return pystray.Menu(
            pystray.MenuItem(
                status_texts.get(status, "Status: Unknown"),
                None, enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Dashboard", self.open_dashboard, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start", self.start_axion,
                visible=status in ("stopped", "unhealthy"),
            ),
            pystray.MenuItem(
                "Stop", self.stop_axion,
                visible=status in ("healthy", "unhealthy", "starting"),
            ),
            pystray.MenuItem(
                "Restart", self.restart_axion,
                visible=status in ("healthy", "unhealthy"),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("View Logs", self.view_logs),
            pystray.MenuItem("Open Data Folder", self.open_data),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Axion", self.quit_app),
        )

    def _update_status(self, status):
        with self._lock:
            if self.status == status:
                return
            prev = self.status
            self.status = status

        labels = {
            "healthy": "Axion — Running",
            "unhealthy": "Axion — Warning",
            "stopped": "Axion — Stopped",
            "starting": "Axion — Starting...",
            "setup": "Axion — Setting up...",
        }
        self.icon.title = labels.get(status, "Axion")
        self.icon.icon = load_tray_icon(status if status != "setup" else "starting")
        self.icon.menu = self._build_menu(status)

        # Notify on meaningful transitions
        with self._lock:
            if status == "healthy" and prev != "healthy":
                self._restart_count = 0  # Reset on healthy
                if not self._first_healthy:
                    self._first_healthy = True
                    notify("Axion is ready! Opening dashboard...")
                    log.info("First healthy — opening dashboard")
                    self._open_dashboard_safe()
            elif status == "stopped" and prev in ("healthy", "unhealthy"):
                if self._user_stopped:
                    notify("Server stopped.")

        log.info(f"Status: {prev} -> {status}")

    def _close_log_handles(self):
        """Close any open log file handles."""
        for fh in self._log_handles:
            try:
                fh.close()
            except Exception:
                pass
        self._log_handles.clear()

    def _health_loop(self):
        """Background thread: checks health every 8 seconds + auto-restart."""
        while self._running:
            try:
                r = requests.get(HEALTH_URL, timeout=3)
                if r.status_code == 200:
                    self._update_status("healthy")
                else:
                    self._update_status("unhealthy")
            except requests.ConnectionError:
                with self._lock:
                    in_transition = self.status in ("starting", "setup")
                    was_running = self.status in ("healthy", "unhealthy")
                    user_stopped = self._user_stopped

                if not in_transition:
                    self._update_status("stopped")

                # Auto-restart if server died unexpectedly
                if was_running and not user_stopped and not in_transition:
                    self._try_auto_restart()
            except Exception:
                log.debug("Health check error", exc_info=True)
            time.sleep(8)

    def _try_auto_restart(self):
        """Attempt auto-restart with exponential backoff."""
        with self._lock:
            if self._restart_count >= self._max_restarts:
                if self._restart_count == self._max_restarts:
                    self._restart_count += 1  # Only notify once
                    stderr_path = LOG_DIR / "kleitos-stderr.log"
                    notify(f"Server keeps crashing. Shutting down. Logs: {stderr_path}")
                    log.error(f"Max auto-restarts ({self._max_restarts}) exceeded. Quitting tray app.")
                    # Quit the tray app to release the mutex and let the user try again
                    threading.Thread(target=lambda: (time.sleep(3), self.quit_app()), daemon=True).start()
                return
            self._restart_count += 1
            count = self._restart_count

        delay = min(2 ** (count - 1) * 5, 120)
        log.info(f"Auto-restart attempt {count}/{self._max_restarts} in {delay}s")
        notify(f"Server crashed. Restarting in {delay}s... ({count}/{self._max_restarts})")
        time.sleep(delay)

        if self._running and self.status == "stopped":
            self._user_stopped = False
            self.start_axion()

    def _get_python(self):
        venv_python = VENV_DIR / "Scripts" / "python.exe"
        if venv_python.exists():
            return str(venv_python)
        return sys.executable

    def _is_server_running(self):
        try:
            r = requests.get(HEALTH_URL, timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def open_dashboard(self, icon=None, item=None):
        webbrowser.open(DASHBOARD_URL)

    def _open_dashboard_safe(self):
        """Open dashboard with retry and fallback notification."""
        def _open():
            time.sleep(1)  # Brief delay to let server fully stabilize
            try:
                webbrowser.open(DASHBOARD_URL)
                log.info(f"Dashboard opened: {DASHBOARD_URL}")
            except Exception as e:
                log.warning(f"Browser open failed: {e}")
                notify(f"Dashboard ready at {DASHBOARD_URL}")
        threading.Thread(target=_open, daemon=True).start()

    def start_axion(self, icon=None, item=None):
        with self._lock:
            if self.status in ("starting", "setup"):
                return
            self._user_stopped = False
        self._update_status("starting")

        def _start():
            try:
                # First-time setup if needed
                if needs_setup():
                    self._update_status("setup")
                    if not run_first_time_setup():
                        self._update_status("stopped")
                        return
                    self._update_status("starting")

                python = self._get_python()
                env = os.environ.copy()
                env["KLEITOS_DATA_DIR"] = str(DATA_DIR)
                env["KLEITOS_DB_PATH"] = str(DATA_DIR / "db" / "kleitos.db")
                env["PATH"] = f"{VENV_DIR / 'Scripts'};{env.get('PATH', '')}"

                # Rotate logs before opening
                _rotate_log(LOG_DIR / "kleitos-stdout.log")
                _rotate_log(LOG_DIR / "kleitos-stderr.log")

                # Close any previously leaked handles
                self._close_log_handles()

                stdout_fh = open(LOG_DIR / "kleitos-stdout.log", "a")
                stderr_fh = open(LOG_DIR / "kleitos-stderr.log", "a")
                self._log_handles = [stdout_fh, stderr_fh]

                self.server_proc = subprocess.Popen(
                    [python, "-m", "uvicorn", "src.main:app",
                     "--host", "127.0.0.1", "--port", str(PORT)],
                    cwd=str(PROJECT_DIR),
                    env=env,
                    stdout=stdout_fh,
                    stderr=stderr_fh,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )

                # Save PID
                PID_FILE.write_text(str(self.server_proc.pid))
                log.info(f"Server started (PID {self.server_proc.pid})")

                # Wait for health
                for i in range(45):
                    if self.server_proc.poll() is not None:
                        log.error("Server process died during startup")
                        self._close_log_handles()
                        self._update_status("stopped")
                        stderr_path = LOG_DIR / "kleitos-stderr.log"
                        notify(f"Server failed to start. Logs: {stderr_path}")
                        return
                    try:
                        r = requests.get(HEALTH_URL, timeout=2)
                        if r.status_code == 200:
                            self._update_status("healthy")
                            return
                    except Exception:
                        pass
                    time.sleep(1)

                self._update_status("unhealthy")
                notify("Server started but health check is failing.")
            except Exception as e:
                log.error(f"Start failed: {e}", exc_info=True)
                self._close_log_handles()
                self._update_status("stopped")

        threading.Thread(target=_start, daemon=True).start()

    def stop_axion(self, icon=None, item=None):
        with self._lock:
            self._user_stopped = True

        def _stop():
            try:
                # Kill our tracked process
                if self.server_proc and self.server_proc.poll() is None:
                    self.server_proc.terminate()
                    try:
                        self.server_proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.server_proc.kill()
                    self.server_proc = None

                # Close log handles
                self._close_log_handles()

                # Kill by PID file (with validation)
                if PID_FILE.exists():
                    try:
                        pid = int(PID_FILE.read_text().strip())
                        if _is_axion_pid(pid):
                            subprocess.run(
                                ["taskkill", "/F", "/PID", str(pid)],
                                capture_output=True, timeout=5,
                                creationflags=subprocess.CREATE_NO_WINDOW,
                            )
                    except Exception:
                        pass
                    PID_FILE.unlink(missing_ok=True)

                # Kill any stray uvicorn on our port
                try:
                    result = subprocess.run(
                        ["netstat", "-ano"],
                        capture_output=True, text=True, timeout=5,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    for line in result.stdout.splitlines():
                        if f":{PORT}" in line and "LISTENING" in line:
                            parts = line.split()
                            pid = parts[-1]
                            if pid.isdigit() and _is_axion_pid(int(pid)):
                                subprocess.run(
                                    ["taskkill", "/F", "/PID", pid],
                                    capture_output=True, timeout=5,
                                    creationflags=subprocess.CREATE_NO_WINDOW,
                                )
                except Exception:
                    pass

                time.sleep(1)
                self._update_status("stopped")
            except Exception as e:
                log.error(f"Stop failed: {e}", exc_info=True)

        threading.Thread(target=_stop, daemon=True).start()

    def restart_axion(self, icon=None, item=None):
        def _restart():
            self.stop_axion()
            time.sleep(3)
            self.start_axion()
        threading.Thread(target=_restart, daemon=True).start()

    def view_logs(self, icon=None, item=None):
        log_file = LOG_DIR / "kleitos-stderr.log"
        if log_file.exists():
            os.startfile(str(log_file))
        else:
            os.startfile(str(LOG_DIR))

    def open_data(self, icon=None, item=None):
        os.startfile(str(DATA_DIR))

    def quit_app(self, icon=None, item=None):
        log.info("Quitting tray app")
        self._running = False
        # Stop server before quitting
        if self.server_proc and self.server_proc.poll() is None:
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
        self._close_log_handles()
        PID_FILE.unlink(missing_ok=True)
        self.icon.stop()

    def _on_setup(self, icon):
        """Called after icon is visible. Handles auto-start."""
        # Rotate tray log at startup
        _rotate_log(LOG_DIR / "tray.log")

        # Start health monitor
        threading.Thread(target=self._health_loop, daemon=True).start()

        # Auto-start server if requested and not already running
        if self._autostart and not self._is_server_running():
            self.start_axion()
        elif self._is_server_running():
            self._update_status("healthy")

    def run(self):
        log.info("Axion tray app starting")
        self.icon.run(setup=self._on_setup)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Single-instance guard with health-aware recovery
    if not _acquire_single_instance():
        # Another tray instance holds the mutex. Check if the server is actually healthy.
        if _is_server_healthy():
            # Server is truly running — just open the dashboard
            notify("Axion is running. Opening dashboard.")
            webbrowser.open(DASHBOARD_URL)
            sys.exit(0)
        else:
            # Mutex held but server is NOT healthy — stale/broken instance.
            # Kill stale processes and retry the mutex.
            log.warning("Stale instance detected (mutex held but server not healthy). Recovering...")
            _kill_stale_tray()
            time.sleep(2)
            if not _acquire_single_instance():
                # Still can't get mutex — truly another instance, just not healthy yet
                notify("Axion is starting up. Please wait a moment.")
                sys.exit(0)
            log.info("Recovered from stale instance. Proceeding with fresh start.")

    # Clean stale PID file from previous failed runs
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            os.kill(old_pid, 0)  # Check if process exists (signal 0 = no-op)
        except (ValueError, OSError, ProcessLookupError):
            PID_FILE.unlink(missing_ok=True)
            log.info("Cleaned stale PID file")

    autostart = "--no-autostart" not in sys.argv
    AxionTray(autostart=autostart).run()
