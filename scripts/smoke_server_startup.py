#!/usr/bin/env python3
"""
Cross-platform live-server startup smoke.

Starts uvicorn as a real OS subprocess on 127.0.0.1:<port>, waits for the
health endpoint to respond, verifies the dashboard returns HTML, then stops
the process cleanly. Works identically on Windows and macOS/Linux.

Used by .github/workflows/release-local-app.yml. Can also be run locally:

    python scripts/smoke_server_startup.py

Or with a custom port:

    AXION_PORT=17888 python scripts/smoke_server_startup.py

Exit code 0 = OK, non-zero = at least one check failed.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = int(os.environ.get("AXION_PORT", "17777"))
HOST = "127.0.0.1"
HEALTH_TIMEOUT_S = 45  # seconds to wait for /api/v1/health
SHUTDOWN_TIMEOUT_S = 15  # seconds to wait for clean shutdown


def _find_free_port(start: int) -> int:
    """Return the first free port >= start (in [start, start+50))."""
    for candidate in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, candidate))
                return candidate
            except OSError:
                continue
    raise RuntimeError(f"no free port found in [{start}, {start + 50})")


def _http_get(url: str, timeout: float = 2.0) -> tuple[int, str]:
    """GET url, return (status_code, body). status -1 on connection error."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, body
    except Exception:
        return -1, ""


def _wait_for_health(url: str, timeout_s: int, proc: subprocess.Popen) -> bool:
    """Poll url until status == 200 or timeout expires. Return True on success."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            print(f"  [FAIL] uvicorn exited before becoming healthy (returncode={proc.returncode})")
            return False
        status, _body = _http_get(url, timeout=1.5)
        if status == 200:
            return True
        time.sleep(1)
    return False


def _stop_process(proc: subprocess.Popen, timeout_s: int) -> bool:
    """Stop proc cleanly. Returns True if it exited within timeout."""
    if proc.poll() is not None:
        return True
    # terminate() sends SIGTERM on POSIX and CTRL_BREAK_EVENT-equivalent on Windows
    # (well, actually a TerminateProcess on Windows — uvicorn handles both gracefully).
    proc.terminate()
    try:
        proc.wait(timeout=timeout_s)
        return True
    except subprocess.TimeoutExpired:
        print("  [WARN] terminate() did not stop process — killing")
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False
        return True


def main() -> int:
    print("=" * 60)
    print("Axion live-server startup smoke")
    print("=" * 60)

    # ── Set up an isolated temp data dir (no contamination of user state) ───
    tmp_root = Path(tempfile.mkdtemp(prefix="axion-server-smoke-"))
    data_dir = tmp_root / "data"
    (data_dir / "db").mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)

    # ── Pick a free port (caller can hint via AXION_PORT) ───────────────────
    try:
        port = _find_free_port(DEFAULT_PORT)
    except RuntimeError as e:
        print(f"  [FAIL] {e}")
        return 1
    base = f"http://{HOST}:{port}"
    print(f"  data dir : {data_dir}")
    print(f"  port     : {port}")
    print(f"  base url : {base}")
    print()

    env = os.environ.copy()
    env.update({
        "AXION_DATA_DIR": str(data_dir),
        "AXION_DB_PATH": str(data_dir / "db" / "kleitos.db"),
        "KLEITOS_DATA_DIR": str(data_dir),
        "KLEITOS_DB_PATH": str(data_dir / "db" / "kleitos.db"),
        "AXION_LOG_LEVEL": "WARNING",
        # Force python output unbuffered so CI logs stream live
        "PYTHONUNBUFFERED": "1",
    })

    cmd = [
        sys.executable, "-m", "uvicorn",
        "src.main:app",
        "--host", HOST,
        "--port", str(port),
        "--log-level", "warning",
    ]

    log_path = tmp_root / "uvicorn.log"
    print(f"  starting : {' '.join(cmd)}")
    print(f"  log      : {log_path}")
    print()

    fail_count = 0

    log_fh = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )

    try:
        # ── 1. Health ────────────────────────────────────────────────────────
        if _wait_for_health(f"{base}/api/v1/health", HEALTH_TIMEOUT_S, proc):
            print(f"  [PASS] health endpoint responded within {HEALTH_TIMEOUT_S}s")
        else:
            print(f"  [FAIL] health endpoint did not respond within {HEALTH_TIMEOUT_S}s")
            fail_count += 1

        # ── 2. Health body shape ────────────────────────────────────────────
        status, body = _http_get(f"{base}/api/v1/health")
        if status == 200 and "\"database\"" in body and "\"version\"" in body:
            print("  [PASS] health response has expected fields")
        else:
            print(f"  [FAIL] health response unexpected: status={status}, body={body[:120]}")
            fail_count += 1

        # ── 3. Dashboard ────────────────────────────────────────────────────
        status, body = _http_get(f"{base}/dashboard/")
        if status == 200 and "<title" in body.lower() and "axion" in body.lower():
            print("  [PASS] dashboard returns HTML containing Axion title")
        else:
            print(f"  [FAIL] dashboard unexpected: status={status}, body={body[:120]}")
            fail_count += 1

        # ── 4. Portfolios (default seeded) ──────────────────────────────────
        status, body = _http_get(f"{base}/api/v1/portfolios")
        if status == 200 and "\"id\":\"default\"" in body.replace(" ", ""):
            print("  [PASS] default portfolio is present")
        else:
            print(f"  [FAIL] /api/v1/portfolios unexpected: status={status}, body={body[:120]}")
            fail_count += 1

    finally:
        # ── 5. Clean shutdown ───────────────────────────────────────────────
        ok = _stop_process(proc, SHUTDOWN_TIMEOUT_S)
        log_fh.close()
        if ok:
            print(f"  [PASS] uvicorn stopped cleanly (returncode={proc.returncode})")
        else:
            print("  [FAIL] uvicorn did not stop cleanly")
            fail_count += 1

        # ── 6. After-stop probe ─────────────────────────────────────────────
        # Wait briefly for the OS to release the port, then confirm we get no response.
        time.sleep(1)
        status, _ = _http_get(f"{base}/api/v1/health", timeout=1.0)
        if status == -1:
            print("  [PASS] no response after shutdown (server is gone)")
        else:
            print(f"  [FAIL] server still responding after shutdown: status={status}")
            fail_count += 1

        # Surface the uvicorn log on failure
        if fail_count:
            print()
            print("=== uvicorn log (last 80 lines) ===")
            try:
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines[-80:]:
                    print(f"  | {line}")
            except Exception as e:
                print(f"  (could not read log: {e})")

        # Clean up temp dir
        try:
            shutil.rmtree(tmp_root, ignore_errors=True)
        except Exception:
            pass

    print()
    print("=" * 60)
    if fail_count:
        print(f"FAILED: {fail_count} check(s) failed")
        return 1
    print("OK: all live-server checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
