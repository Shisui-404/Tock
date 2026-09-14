"""Alarm domain objects and recurrence calculations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum


class RepeatMode(str, Enum):
    """Supported alarm recurrence modes."""

    ONCE = "once"
    DAILY = "daily"
    WEEKDAYS = "weekdays"
    WEEKLY = "weekly"


@dataclass
class Alarm:
    """A persisted alarm. Datetimes are naive local time by design."""

    id: str
    time: str
    repeat: RepeatMode
    next_fire_at: datetime | None
    label: str = ""
    weekday: int | None = None
    enabled: bool = True
    snoozed_until: datetime | None = None
    last_fired_at: datetime | None = None
    created_at: datetime = field(default_factory=datetime.now)

    def effective_fire_at(self) -> datetime | None:
        """Return the currently actionable time, preferring a snooze."""
        return self.snoozed_until or self.next_fire_at

    @property
    def status(self) -> str:
        if not self.enabled:
            return "disabled"
        if self.snoozed_until is not None:
            return "snoozed"
        if self.repeat is RepeatMode.ONCE and self.next_fire_at is None:
            return "done"
        return "active"

    def scheduled_time(self) -> time:
        hour, minute, second = (int(part) for part in self.time.split(":"))
        return time(hour, minute, second)


def next_occurrence(alarm: Alarm, after: datetime) -> datetime:
    """Return the first recurring occurrence strictly after ``after``.

    The function is pure to keep clock edge cases straightforward to test.
    """
    if alarm.repeat is RepeatMode.ONCE:
        raise ValueError("a one-off alarm has no subsequent occurrence")

    target_time = alarm.scheduled_time()
    candidate = datetime.combine(after.date(), target_time)

    if alarm.repeat is RepeatMode.DAILY:
        return candidate if candidate > after else candidate + timedelta(days=1)

    if alarm.repeat is RepeatMode.WEEKDAYS:
        while candidate <= after or candidate.weekday() > 4:
            candidate += timedelta(days=1)
        return candidate

    if alarm.repeat is RepeatMode.WEEKLY:
        if alarm.weekday is None or not 0 <= alarm.weekday <= 6:
            raise ValueError("a weekly alarm requires a weekday from 0 to 6")
        days_until = (alarm.weekday - candidate.weekday()) % 7
        candidate += timedelta(days=days_until)
        if candidate <= after:
            candidate += timedelta(days=7)
        return candidate

    raise ValueError(f"unsupported repeat mode: {alarm.repeat}")
