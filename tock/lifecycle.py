"""Starting the daemon in the background and stopping a running daemon."""

from __future__ import annotations

import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from .compat.process import process_alive, spawn_detached
from .control import ControlChannel
from .scheduler import DaemonAlreadyRunningError


class DaemonLifecycleError(RuntimeError):
    """Raised when the daemon cannot be started, stopped, or reached."""


def start_background(directory: Path, daemon_args: list[str], *, timeout: float = 10) -> int | None:
    """Launch ``tock daemon`` detached from this terminal and wait until it owns the lock."""
    control = ControlChannel(directory)
    if control.daemon_running():
        raise DaemonAlreadyRunningError(_already_running(control.daemon_pid()))
    control.clear_pid()  # a stale file from a crashed daemon must not look like success
    env = dict(os.environ, TOCK_HOME=str(directory))
    package_root = Path(__file__).resolve().parent.parent  # `-m` imports from the working directory
    command = [sys.executable, "-u", "-m", "tock", "daemon", *daemon_args]
    with control.log_path.open("a", encoding="utf-8") as log:
        log.write(f"--- starting {datetime.now():%Y-%m-%d %H:%M:%S} ---\n")
        log.flush()
        process = spawn_detached(command, log=log, cwd=package_root, env=env)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pid = control.daemon_pid()
        if pid is not None:
            process.returncode = 0  # left running on purpose; silences Popen's "still running" warning
            return pid
        if process.poll() is not None:
            raise DaemonLifecycleError(f"daemon exited during startup; see {control.log_path}")
        time.sleep(0.1)
    raise DaemonLifecycleError(f"daemon did not start within {timeout:g}s; see {control.log_path}")


def stop_daemon(directory: Path, *, timeout: float = 5) -> int | None:
    """Ask the daemon to exit, terminating it only if it does not respond."""
    control = ControlChannel(directory)
    if not control.daemon_running():
        raise DaemonLifecycleError("daemon is not running")
    pid = control.daemon_pid()
    control.send("stop")
    if _wait_until_stopped(control, pid, timeout):
        return pid
    if pid is None:
        raise DaemonLifecycleError("daemon did not respond to the stop request")
    os.kill(pid, signal.SIGTERM)
    if _wait_until_stopped(control, pid, 2):
        control.clear_pid()
        return pid
    raise DaemonLifecycleError(f"daemon (pid {pid}) did not stop")


def _wait_until_stopped(control: ControlChannel, pid: int | None, timeout: float) -> bool:
    # The daemon releases its lock before the process exits, and until it exits it
    # still holds daemon.log open, which Windows refuses to delete or move.
    def stopped() -> bool:
        return not control.daemon_running() and (pid is None or not process_alive(pid))

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stopped():
            return True
        time.sleep(0.1)
    return stopped()


def _already_running(pid: int | None) -> str:
    return f"daemon already running (pid {pid})" if pid else "daemon already running"
