"""Lightweight watchdog: keep the Flask dashboard running on localhost.

Run at Windows login via Task Scheduler (see README "Standalone App").
Does not open a browser — use launch.bat for interactive sessions.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
APP_PY = PROJECT_ROOT / "app.py"
SERVER_LOCK = PROJECT_ROOT / ".flask.server.lock"
HOST = "127.0.0.1"
PORT = int(os.getenv("GUI_PORT", os.getenv("FLASK_PORT", "5000")))
CHECK_INTERVAL = float(os.getenv("WATCHDOG_INTERVAL_SEC", "30"))
STARTUP_GRACE_SEC = float(os.getenv("WATCHDOG_STARTUP_GRACE_SEC", "8"))


def _read_server_lock_pid() -> int | None:
    try:
        raw = SERVER_LOCK.read_text(encoding="ascii").strip()
        if raw.isdigit():
            return int(raw)
    except OSError:
        pass
    return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def launch_in_progress() -> bool:
    """True when launch.ps1 or app.py is already starting the server."""
    lock_pid = _read_server_lock_pid()
    return bool(lock_pid and _pid_alive(lock_pid))


def port_listening() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=1):
            return True
    except OSError:
        return False


def server_healthy() -> bool:
    """Port is open and /api/bot/status returns 200."""
    if not port_listening():
        return False
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"http://{HOST}:{PORT}/api/bot/status", timeout=3
        ) as resp:
            return resp.status == 200
    except OSError:
        return False


def start_server() -> None:
    if not PYTHON.exists():
        print(f"Watchdog: Python not found at {PYTHON}", file=sys.stderr)
        sys.exit(1)
    env = os.environ.copy()
    env["SOLANA_AUTO_OPEN_BROWSER"] = "0"
    env["SOLANA_LAUNCHED_BY"] = "watchdog"
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    subprocess.Popen(
        [str(PYTHON), str(APP_PY)],
        cwd=str(PROJECT_ROOT),
        env=env,
        creationflags=flags,
    )


def main() -> None:
    print(f"Watchdog: monitoring {HOST}:{PORT} every {CHECK_INTERVAL:.0f}s (Ctrl+C to stop)")
    while True:
        if not server_healthy():
            if port_listening():
                print("Watchdog: port open but bot not healthy - waiting for recovery")
            elif launch_in_progress():
                print("Watchdog: launcher or app.py is starting the server - waiting")
            else:
                print("Watchdog: server not responding - starting app.py")
                start_server()
                time.sleep(STARTUP_GRACE_SEC)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nWatchdog stopped.")
