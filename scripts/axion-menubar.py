#!/usr/bin/env python3
"""
Axion Menu Bar App — the premium macOS launcher.

Run this and Axion appears in the menu bar. The server starts automatically.

  - Branded "K" in the menu bar with live status
  - Open Dashboard (default action)
  - Start / Stop / Restart server
  - View Logs / Open Data Folder
  - First-launch auto-setup (venv + dependencies)
  - Auto-restart on crash with exponential backoff
  - Single-instance guard (only one menubar app at a time)
  - Quit (stops server and exits)

Install:  pip3 install rumps requests
Run:      python3 scripts/axion-menubar.py
"""

import fcntl
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

# ---------------------------------------------------------------------------
# Single-instance guard (fcntl file lock)
# ---------------------------------------------------------------------------
_lock_file = None
_lock_fd = None


def _acquire_single_instance():
    """Returns True if this is the only instance, False if another is running."""
    global _lock_file, _lock_fd
    lock_path = Path.home() / "kleitos-data" / "menubar.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _lock_fd = open(lock_path, "w")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
        _lock_file = lock_path
        return True
    except (IOError, OSError):
        return False


# ---------------------------------------------------------------------------
# Ensure dependencies (rumps, requests)
# ---------------------------------------------------------------------------
try:
    import rumps
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "rumps", "-q"])
    import rumps

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests


# ---------------------------------------------------------------------------
# Constants (port is configurable via KLEITOS_PORT env var)
# ---------------------------------------------------------------------------
PORT = int(os.environ.get("KLEITOS_PORT", 7777))
HEALTH_URL = f"http://localhost:{PORT}/api/v1/health"
DASHBOARD_URL = f"http://localhost:{PORT}"

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
VENV_DIR = PROJECT_DIR / ".venv"
ASSETS_DIR = PROJECT_DIR / "assets"
DATA_DIR = Path.home() / "kleitos-data"
PID_FILE = DATA_DIR / "kleitos.pid"
LOG_DIR = DATA_DIR / "logs"

# Ensure data directories
for d in [DATA_DIR / "db", LOG_DIR, DATA_DIR / "backups"]:
    d.mkdir(parents=True, exist_ok=True)

# Logging
logging.basicConfig(
    filename=str(LOG_DIR / "menubar.log"),
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("axion-menubar")


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
        for i in range(max_backups, 0, -1):
            src = path.with_suffix(f"{path.suffix}.{i}")
            dst = path.with_suffix(f"{path.suffix}.{i + 1}")
            if i == max_backups and src.exists():
                src.unlink()
            elif src.exists():
                src.rename(dst)
        path.rename(path.with_suffix(f"{path.suffix}.1"))
        log.info(f"Rotated log: {path.name}")
    except Exception as e:
        log.debug(f"Log rotation failed for {path}: {e}")


# ---------------------------------------------------------------------------
# PID validation
# ---------------------------------------------------------------------------
def _is_axion_pid(pid):
    """Check if a PID belongs to an Axion/uvicorn process."""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
        output = result.stdout.lower()
        return "uvicorn" in output or "python" in output
    except Exception:
        return False


def _command_exists(cmd):
    try:
        subprocess.run(
            [cmd, "--version"], capture_output=True, timeout=5,
        )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# First-launch auto-setup
# ---------------------------------------------------------------------------
def needs_setup():
    """Check if venv + dependencies need to be installed or repaired."""
    python = VENV_DIR / "bin" / "python"
    if not python.exists():
        return True
    # Verify venv is functional
    try:
        result = subprocess.run(
            [str(python), "-c", "import fastapi, uvicorn"],
            capture_output=True, timeout=15,
        )
        return result.returncode != 0
    except Exception:
        return True


def run_first_time_setup():
    """Create venv and install dependencies. Returns True on success."""
    log.info("Running first-time setup...")
    rumps.notification("Axion", "", "First launch — setting up (1-2 minutes)...")

    # Remove broken venv if it exists
    if VENV_DIR.exists():
        log.info("Removing broken venv...")
        shutil.rmtree(VENV_DIR, ignore_errors=True)

    # Find system Python
    sys_python = None
    for cmd in ["python3.12", "python3.11", "python3"]:
        try:
            result = subprocess.run(
                [cmd, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                m = re.search(r"(\d+)\.(\d+)", result.stdout + result.stderr)
                if m and int(m.group(1)) >= 3 and int(m.group(2)) >= 11:
                    sys_python = cmd
                    log.info(f"Found: {result.stdout.strip()}")
                    break
        except Exception:
            continue

    # Try Homebrew install
    if not sys_python:
        log.info("Python 3.11+ not found — installing via Homebrew")
        rumps.notification("Axion", "", "Installing Python 3.12...")
        try:
            if not _command_exists("brew"):
                subprocess.run(
                    ["/bin/bash", "-c",
                     "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"],
                    timeout=300, stdin=subprocess.DEVNULL,
                )
            subprocess.run(["brew", "install", "python@3.12"], timeout=300)
            brew_python = subprocess.run(
                ["brew", "--prefix", "python@3.12"],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            sys_python = f"{brew_python}/bin/python3.12"
        except Exception as e:
            log.error(f"Homebrew install failed: {e}")

    if not sys_python or not _command_exists(sys_python):
        log.error("Could not find or install Python 3.11+")
        rumps.alert("Python 3.11+ is required.\n\nInstall from python.org and try again.")
        return False

    # Create venv
    rumps.notification("Axion", "", "Creating virtual environment...")
    log.info("Creating virtual environment...")
    result = subprocess.run(
        [sys_python, "-m", "venv", str(VENV_DIR)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        log.error(f"venv creation failed: {result.stderr}")
        return False

    venv_python = str(VENV_DIR / "bin" / "python")

    # Upgrade pip
    subprocess.run(
        [venv_python, "-m", "pip", "install", "--upgrade", "pip", "-q"],
        capture_output=True, timeout=120,
    )

    # Install requirements
    rumps.notification("Axion", "", "Installing dependencies (1-2 min)...")
    log.info("Installing dependencies...")
    result = subprocess.run(
        [venv_python, "-m", "pip", "install", "-r", str(PROJECT_DIR / "requirements.txt"), "-q"],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        log.error(f"pip install failed: {result.stderr}")
        return False

    # Verify
    rumps.notification("Axion", "", "Verifying installation...")
    result = subprocess.run(
        [venv_python, "-c", "import fastapi, uvicorn, sqlalchemy, aiosqlite, apscheduler"],
        capture_output=True, timeout=30,
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
    rumps.notification("Axion", "", "Setup complete! Starting Axion...")
    return True


# ---------------------------------------------------------------------------
# AxionMenuBar
# ---------------------------------------------------------------------------
class AxionMenuBar(rumps.App):
    def __init__(self, autostart=True):
        super().__init__("Axion", title="A", quit_button=None)
        self._lock = threading.Lock()
        self.status = "unknown"
        self._autostart = autostart
        self._first_healthy = False
        self.server_proc = None
        self._log_handles = []  # Track open log file handles

        # Auto-restart state
        self._restart_count = 0
        self._max_restarts = 5
        self._user_stopped = False  # True if user explicitly stopped

        self.status_item = rumps.MenuItem("Status: Checking...")
        self.status_item.set_callback(None)

        self.start_item = rumps.MenuItem("Start", callback=self.start_axion)
        self.stop_item = rumps.MenuItem("Stop", callback=self.stop_axion)
        self.restart_item = rumps.MenuItem("Restart", callback=self.restart_axion)

        self.menu = [
            self.status_item,
            None,
            rumps.MenuItem("Open Dashboard", callback=self.open_dashboard),
            None,
            self.start_item,
            self.stop_item,
            self.restart_item,
            None,
            rumps.MenuItem("View Logs", callback=self.view_logs),
            rumps.MenuItem("Open Data Folder", callback=self.open_data),
            None,
            rumps.MenuItem("Quit Axion", callback=self.quit_app),
        ]

        self._timer = rumps.Timer(self._health_check, 8)
        self._timer.start()

        # Rotate menubar log at startup
        _rotate_log(LOG_DIR / "menubar.log")

        # Kick off auto-start
        if autostart:
            threading.Thread(target=self._auto_start_flow, daemon=True).start()
        else:
            threading.Thread(target=self._health_check, args=(None,), daemon=True).start()

    def _auto_start_flow(self):
        """Handle first-time setup and auto-start."""
        if self._is_server_running():
            self._set_status("healthy")
            return

        if needs_setup():
            self._set_status("setup")
            if not run_first_time_setup():
                self._set_status("stopped")
                return

        self.start_axion(None)

    def _is_server_running(self):
        try:
            r = requests.get(HEALTH_URL, timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def _health_check(self, _):
        try:
            r = requests.get(HEALTH_URL, timeout=3)
            if r.status_code == 200:
                self._set_status("healthy")
            else:
                self._set_status("unhealthy")
        except requests.ConnectionError:
            with self._lock:
                in_transition = self.status in ("starting", "setup")
                was_running = self.status in ("healthy", "unhealthy")
                user_stopped = self._user_stopped

            if not in_transition:
                self._set_status("stopped")

            # Auto-restart if server died unexpectedly
            if was_running and not user_stopped and not in_transition:
                self._try_auto_restart()
        except Exception:
            log.debug("Health check error", exc_info=True)

    def _try_auto_restart(self):
        """Attempt auto-restart with exponential backoff."""
        with self._lock:
            if self._restart_count >= self._max_restarts:
                if self._restart_count == self._max_restarts:
                    self._restart_count += 1  # Only notify once
                    rumps.notification("Axion", "", "Server keeps crashing. Check logs.")
                    log.error(f"Max auto-restarts ({self._max_restarts}) exceeded")
                return
            self._restart_count += 1
            count = self._restart_count

        delay = min(2 ** (count - 1) * 5, 120)
        log.info(f"Auto-restart attempt {count}/{self._max_restarts} in {delay}s")
        rumps.notification("Axion", "", f"Server crashed. Restarting in {delay}s... ({count}/{self._max_restarts})")
        time.sleep(delay)

        if self.status == "stopped":
            self._user_stopped = False
            self.start_axion(None)

    def _set_status(self, status):
        with self._lock:
            if self.status == status:
                return
            prev = self.status
            self.status = status

        labels = {
            "healthy":   ("K  Running", "Status: Running"),
            "unhealthy": ("K  Warning", "Status: Unhealthy"),
            "stopped":   ("K  Stopped", "Status: Not Running"),
            "starting":  ("K  Starting...", "Status: Starting..."),
            "setup":     ("K  Setup...", "Status: Setting up..."),
            "unknown":   ("K  ...",     "Status: Checking..."),
        }
        title, detail = labels.get(status, labels["unknown"])
        self.title = title
        self.status_item.title = detail

        # Show/hide menu items based on status
        self.start_item.set_callback(
            self.start_axion if status in ("stopped", "unhealthy") else None
        )
        self.stop_item.set_callback(
            self.stop_axion if status in ("healthy", "unhealthy", "starting") else None
        )
        self.restart_item.set_callback(
            self.restart_axion if status in ("healthy", "unhealthy") else None
        )

        # Notify on meaningful transitions
        with self._lock:
            if status == "healthy" and prev != "healthy":
                self._restart_count = 0  # Reset on healthy
                if not self._first_healthy:
                    self._first_healthy = True
                    rumps.notification("Axion", "", "Axion is ready!")
            elif status == "stopped" and prev in ("healthy", "unhealthy"):
                if self._user_stopped:
                    rumps.notification("Axion", "", "Server stopped.")

        log.info(f"Status: {prev} -> {status}")

    def _close_log_handles(self):
        """Close any open log file handles."""
        for fh in self._log_handles:
            try:
                fh.close()
            except Exception:
                pass
        self._log_handles.clear()

    def _get_python(self):
        venv_python = VENV_DIR / "bin" / "python"
        if venv_python.exists():
            return str(venv_python)
        return "python3"

    @rumps.clicked("Open Dashboard")
    def open_dashboard(self, _):
        webbrowser.open(DASHBOARD_URL)

    @rumps.clicked("Start")
    def start_axion(self, _):
        with self._lock:
            if self.status in ("starting", "setup"):
                return
            self._user_stopped = False
        self._set_status("starting")

        def _start():
            try:
                if needs_setup():
                    self._set_status("setup")
                    if not run_first_time_setup():
                        self._set_status("stopped")
                        return
                    self._set_status("starting")

                python = self._get_python()
                env = os.environ.copy()
                env["KLEITOS_DATA_DIR"] = str(DATA_DIR)
                env["KLEITOS_DB_PATH"] = str(DATA_DIR / "db" / "kleitos.db")
                env["PATH"] = f"{VENV_DIR / 'bin'}:{env.get('PATH', '')}"

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
                )
                PID_FILE.write_text(str(self.server_proc.pid))
                log.info(f"Server started (PID {self.server_proc.pid})")

                for _ in range(45):
                    if self.server_proc.poll() is not None:
                        log.error("Server process died during startup")
                        self._close_log_handles()
                        self._set_status("stopped")
                        rumps.notification("Axion", "", "Server failed to start. Check View Logs for details.")
                        return
                    try:
                        r = requests.get(HEALTH_URL, timeout=2)
                        if r.status_code == 200:
                            self._set_status("healthy")
                            return
                    except Exception:
                        pass
                    time.sleep(1)

                self._set_status("unhealthy")
                rumps.notification("Axion", "", "Server started but health check is failing.")
            except Exception as e:
                log.error(f"Start failed: {e}", exc_info=True)
                self._close_log_handles()
                self._set_status("stopped")

        threading.Thread(target=_start, daemon=True).start()

    @rumps.clicked("Stop")
    def stop_axion(self, _):
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
                            try:
                                os.kill(pid, signal.SIGTERM)
                                time.sleep(2)
                                try:
                                    os.kill(pid, 0)
                                    os.kill(pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                            except ProcessLookupError:
                                pass
                    except Exception:
                        pass
                    PID_FILE.unlink(missing_ok=True)

                # Kill any strays (narrow pattern with port)
                subprocess.run(
                    ["pkill", "-f", f"uvicorn src.main:app.*--port {PORT}"],
                    capture_output=True, timeout=5,
                )
                time.sleep(1)
                self._set_status("stopped")
            except Exception as e:
                log.error(f"Stop failed: {e}", exc_info=True)

        threading.Thread(target=_stop, daemon=True).start()

    @rumps.clicked("Restart")
    def restart_axion(self, _):
        def _restart():
            self.stop_axion(None)
            time.sleep(3)
            self.start_axion(None)
        threading.Thread(target=_restart, daemon=True).start()

    @rumps.clicked("View Logs")
    def view_logs(self, _):
        log_file = LOG_DIR / "kleitos-stderr.log"
        if log_file.exists():
            subprocess.Popen(["open", "-a", "Console", str(log_file)])
        else:
            subprocess.Popen(["open", str(LOG_DIR)])

    @rumps.clicked("Open Data Folder")
    def open_data(self, _):
        subprocess.Popen(["open", str(DATA_DIR)])

    @rumps.clicked("Quit Axion")
    def quit_app(self, _):
        log.info("Quitting menubar app")
        # Stop server before quitting
        if self.server_proc and self.server_proc.poll() is None:
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
        self._close_log_handles()
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text().strip())
                if _is_axion_pid(pid):
                    os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
            PID_FILE.unlink(missing_ok=True)
        rumps.quit_application()


if __name__ == "__main__":
    # Single-instance guard
    if not _acquire_single_instance():
        rumps.notification("Axion", "", "Axion is already running. Check the menu bar.")
        sys.exit(0)

    autostart = "--no-autostart" not in sys.argv
    AxionMenuBar(autostart=autostart).run()
