"""Clock-injected foreground alarm scheduler."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from .alert import AlertOutcome, AlertSession, disabled_notifier
from .compat.notify import DesktopNotifier
from .compat.locks import FileLock, LockUnavailableError
from .control import ControlChannel
from .models import Alarm, RepeatMode, next_occurrence
from .store import AlarmStore


class DaemonAlreadyRunningError(RuntimeError):
    """Raised when another daemon owns the per-user lock."""


class Scheduler:
    def __init__(self, store: AlarmStore, *, alert: Callable[[Alarm], AlertOutcome] | None = None,
                 clock: Callable[[], datetime] = datetime.now, sleep: Callable[[float], None] = time.sleep,
                 tick_seconds: float = 0.5, ring_timeout: float = 300, snooze_minutes: float = 5,
                 grace_minutes: float = 15, output: Callable[[str], None] = print,
                 control: ControlChannel | None = None, notify: bool = True) -> None:
        self.store = store
        self.clock = clock
        self.sleep = sleep
        self.tick_seconds = tick_seconds
        self.snooze_minutes = snooze_minutes
        self.grace = timedelta(minutes=grace_minutes)
        self.output = output
        self.control = control or ControlChannel(store.directory)
        self.alert = alert or AlertSession(store.directory, ring_timeout=ring_timeout, control=self.control,
                                           notifier_factory=DesktopNotifier if notify else disabled_notifier).run
        self._stopped = False
        self._last_tick: datetime | None = None

    def stop(self) -> None:
        self._stopped = True

    def run(self) -> None:
        """Run until interrupted; only one process may own this scheduler."""
        try:
            with FileLock(self.control.lock_path, blocking=False):
                started = self.control.clock()
                self.control.write_pid()
                try:
                    while not self._stopped:
                        self.tick()
                        # `tock stop`; dismiss/snooze sent while nothing rings are discarded.
                        if self.control.receive(since=started) == "stop":
                            self.stop()
                        if not self._stopped:
                            self.sleep(self.tick_seconds)
                finally:
                    self.control.clear_pid()
                    self.control.clear_ringing()
        except LockUnavailableError as error:
            raise DaemonAlreadyRunningError("daemon already running") from error

    def tick(self) -> list[str]:
        """Fire all currently due alarms, returning their IDs for tests/logging."""
        now = self.clock()
        startup_or_resume = self._last_tick is None or now - self._last_tick > timedelta(seconds=5)
        due = [alarm for alarm in self.store.load() if alarm.enabled and alarm.effective_fire_at() and alarm.effective_fire_at() <= now]
        fired: list[str] = []
        for snapshot in sorted(due, key=lambda alarm: alarm.effective_fire_at() or datetime.max):
            late_by = now - (snapshot.effective_fire_at() or now)
            if startup_or_resume and late_by > self.grace:
                if self._mark_missed(snapshot.id, now) is not None:
                    label = f" '{snapshot.label}'" if snapshot.label else ""
                    self.output(f"Missed {snapshot.time}{label} (by {late_by})")
                continue
            alarm = self._mark_fired(snapshot.id, now)
            if alarm is None:
                continue
            fired.append(alarm.id)
            outcome = self.alert(alarm)
            if outcome is AlertOutcome.SNOOZE:
                self._set_snooze(alarm.id, now + timedelta(minutes=self.snooze_minutes))
            elif outcome is AlertOutcome.SHUTDOWN:
                self.stop()
                break
        self._last_tick = now
        return fired

    def _mark_fired(self, alarm_id: str, now: datetime) -> Alarm | None:
        fired: Alarm | None = None

        def operation(alarms: list[Alarm]) -> None:
            nonlocal fired
            current = next((alarm for alarm in alarms if alarm.id == alarm_id), None)
            if current is None or not current.enabled or current.effective_fire_at() is None or current.effective_fire_at() > now:
                return
            current.last_fired_at = now
            current.snoozed_until = None
            current.next_fire_at = None if current.repeat is RepeatMode.ONCE else next_occurrence(current, now)
            fired = current

        self.store.update(operation)
        return fired

    def _mark_missed(self, alarm_id: str, now: datetime) -> Alarm | None:
        missed: Alarm | None = None

        def operation(alarms: list[Alarm]) -> None:
            nonlocal missed
            current = next((alarm for alarm in alarms if alarm.id == alarm_id), None)
            if current is None or not current.enabled or current.effective_fire_at() is None or current.effective_fire_at() > now:
                return
            current.last_fired_at = now
            current.snoozed_until = None
            current.next_fire_at = None if current.repeat is RepeatMode.ONCE else next_occurrence(current, now)
            missed = current

        self.store.update(operation)
        return missed

    def _set_snooze(self, alarm_id: str, until: datetime) -> None:
        def operation(alarms: list[Alarm]) -> None:
            current = next((alarm for alarm in alarms if alarm.id == alarm_id), None)
            if current is not None and current.enabled:
                current.snoozed_until = until

        self.store.update(operation)
