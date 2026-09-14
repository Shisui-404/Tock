"""File-based coordination between CLI commands and a running daemon.

Commands never talk to the daemon directly. They drop small JSON files in the
data directory, and the daemon picks them up on its next tick.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from .compat.locks import FileLock, LockUnavailableError
from .models import Alarm

DAEMON_LOCK = "daemon.lock"
ACTIONS = ("stop", "dismiss", "snooze")


class ControlChannel:
    def __init__(self, directory: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.directory = directory
        self.clock = clock
        self.lock_path = directory / DAEMON_LOCK
        self.pid_path = directory / "daemon.pid"
        self.log_path = directory / "daemon.log"
        self.request_path = directory / "request.json"
        self.ringing_path = directory / "ringing.json"

    # Daemon presence -----------------------------------------------------

    def daemon_running(self) -> bool:
        """True when some process holds the daemon lock."""
        try:
            with FileLock(self.lock_path, blocking=False):
                return False
        except LockUnavailableError:
            return True

    def daemon_pid(self) -> int | None:
        try:
            return int(self.pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def write_pid(self) -> None:
        _write_text(self.pid_path, f"{os.getpid()}\n")

    def clear_pid(self) -> None:
        _unlink(self.pid_path)

    # Requests ------------------------------------------------------------

    def send(self, action: str) -> None:
        if action not in ACTIONS:
            raise ValueError(f"unknown daemon request: {action}")
        _write_text(self.request_path, json.dumps({"action": action, "sent_at": self.clock()}))

    def receive(self, since: float) -> str | None:
        """Consume the pending request, ignoring any sent before ``since``."""
        claimed = self.request_path.with_name("request.claimed")
        try:
            os.replace(self.request_path, claimed)
        except OSError:  # nothing pending, or a writer still holds it; retry next tick
            return None
        document = _read_json(claimed)
        _unlink(claimed)
        if document is None:
            return None
        action, sent_at = document.get("action"), document.get("sent_at")
        if action in ACTIONS and isinstance(sent_at, (int, float)) and sent_at >= since:
            return action
        return None

    # Ringing marker ------------------------------------------------------

    def mark_ringing(self, alarm: Alarm) -> None:
        _write_text(self.ringing_path, json.dumps({"id": alarm.id, "time": alarm.time, "label": alarm.label}))

    def clear_ringing(self) -> None:
        _unlink(self.ringing_path)

    def ringing(self) -> dict | None:
        return _read_json(self.ringing_path)


def _read_json(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        last_error = None
        for _ in range(5):
            try:
                os.replace(temp_name, path)
                return
            except PermissionError as error:
                last_error = error
                time.sleep(0.05)
        raise last_error or OSError(f"could not replace {path.name}")
    finally:
        _unlink(Path(temp_name))


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:  # already gone, or briefly held open on Windows
        pass
