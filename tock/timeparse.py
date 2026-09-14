"""Locale-independent parsing for command-line alarm times."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

_TIME = re.compile(r"^(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)(?::(?P<second>[0-5]\d))?$")
_RELATIVE = re.compile(r"^\+(?P<amount>[1-9]\d*)(?P<unit>[smh])$")
_DATE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})$")


class TimeParseError(ValueError):
    """Raised for user-facing invalid time input."""


def parse_clock(value: str) -> time:
    """Parse strict 24-hour ``HH:MM[:SS]`` input."""
    match = _TIME.fullmatch(value)
    if not match:
        raise TimeParseError("time must use 24-hour HH:MM or HH:MM:SS format")
    return time(int(match["hour"]), int(match["minute"]), int(match["second"] or 0))


def parse_date(value: str) -> date:
    """Parse ISO ``YYYY-MM-DD`` input without locale-dependent directives."""
    match = _DATE.fullmatch(value)
    if not match:
        raise TimeParseError("date must use YYYY-MM-DD format")
    try:
        return date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError as error:
        raise TimeParseError(str(error)) from error


def parse_relative(value: str, now: datetime) -> datetime:
    """Parse ``+Ns``, ``+Nm`` or ``+Nh`` into a future datetime."""
    match = _RELATIVE.fullmatch(value)
    if not match:
        raise TimeParseError("relative time must use +Ns, +Nm, or +Nh (for example +10m)")
    amount = int(match["amount"])
    unit = {"s": "seconds", "m": "minutes", "h": "hours"}[match["unit"]]
    return now + timedelta(**{unit: amount})


def parse_absolute(clock_value: str, date_value: str | None, now: datetime) -> datetime:
    """Resolve a clock time and optional date to its first valid occurrence."""
    parsed_time = parse_clock(clock_value)
    if date_value is not None:
        result = datetime.combine(parse_date(date_value), parsed_time)
        if result <= now:
            raise TimeParseError("the specified alarm time must be in the future")
        return result

    result = datetime.combine(now.date(), parsed_time)
    return result if result > now else result + timedelta(days=1)
