"""JSON persistence with locked read-modify-write updates."""

from __future__ import annotations

import json
import os
import tempfile
import warnings
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from .compat.locks import FileLock
from .compat.paths import data_directory
from .models import Alarm, RepeatMode

T = TypeVar("T")
STORE_VERSION = 1


class StoreError(RuntimeError):
    """Raised for a store that cannot safely be interpreted."""


class AlarmStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or data_directory()
        self.path = self.directory / "alarms.json"
        self.lock_path = self.directory / "alarms.lock"

    def load(self) -> list[Alarm]:
        """Load alarms, recovering malformed JSON by retaining a dated backup."""
        if not self.path.exists():
            return []
        try:
            with self.path.open(encoding="utf-8") as handle:
                document = json.load(handle)
            if document.get("version") != STORE_VERSION:
                raise StoreError(f"unsupported alarms store version: {document.get('version')!r}")
            return [self._alarm_from_dict(item) for item in document.get("alarms", [])]
        except StoreError:
            raise
        except (OSError, ValueError, TypeError, KeyError) as error:
            backup = self.path.with_name(f"alarms.json.corrupt-{datetime.now():%Y%m%d%H%M%S}")
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            warnings.warn(f"corrupt alarm store was backed up to {backup.name}; starting with no alarms", RuntimeWarning)
            return []

    def update(self, operation: Callable[[list[Alarm]], T]) -> T:
        """Apply an operation to a fresh locked copy, atomically persisting it."""
        with FileLock(self.lock_path):
            alarms = self.load()
            result = operation(alarms)
            self._write(alarms)
            return result

    def _write(self, alarms: list[Alarm]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {"version": STORE_VERSION, "alarms": [self._alarm_to_dict(alarm) for alarm in alarms]}
        descriptor, temp_name = tempfile.mkstemp(prefix=".alarms-", suffix=".tmp", dir=self.directory, text=True)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            last_error = None
            for _ in range(5):
                try:
                    os.replace(temp_name, self.path)
                    return
                except PermissionError as error:
                    last_error = error
            raise last_error or OSError("could not replace alarm store")
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    @staticmethod
    def _alarm_to_dict(alarm: Alarm) -> dict[str, object]:
        def encoded(value: datetime | None) -> str | None:
            return value.isoformat() if value is not None else None

        return {"id": alarm.id, "time": alarm.time, "repeat": alarm.repeat.value,
                "next_fire_at": encoded(alarm.next_fire_at), "label": alarm.label,
                "weekday": alarm.weekday, "enabled": alarm.enabled,
                "snoozed_until": encoded(alarm.snoozed_until), "last_fired_at": encoded(alarm.last_fired_at),
                "created_at": alarm.created_at.isoformat()}

    @staticmethod
    def _alarm_from_dict(data: dict[str, object]) -> Alarm:
        def decoded(name: str) -> datetime | None:
            value = data.get(name)
            return datetime.fromisoformat(value) if value is not None else None

        return Alarm(id=str(data["id"]), time=str(data["time"]), repeat=RepeatMode(str(data["repeat"])),
                     next_fire_at=decoded("next_fire_at"), label=str(data.get("label", "")),
                     weekday=data.get("weekday"), enabled=bool(data.get("enabled", True)),
                     snoozed_until=decoded("snoozed_until"), last_fired_at=decoded("last_fired_at"),
                     created_at=decoded("created_at") or datetime.now())
