"""Terminal capability checks and conservative alert rendering."""

from __future__ import annotations

import os
import sys


def supports_colour(stream=None) -> bool:
    stream = stream or sys.stdout
    return bool(stream.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb")


def supports_unicode(stream=None) -> bool:
    stream = stream or sys.stdout
    try:
        "⏰".encode(stream.encoding or "ascii")
        return True
    except UnicodeEncodeError:
        return False


def alarm_banner(alarm_time: str, label: str, *, inverse: bool = False, stream=None) -> str:
    """Render one portable alarm banner; callers decide how often to redraw."""
    stream = stream or sys.stdout
    icon = "⏰" if supports_unicode(stream) else "[ALARM]"
    message = f"{icon} ALARM {alarm_time}" + (f" — {label}" if label else "")
    if supports_colour(stream):
        decoration = "\033[7;31m" if inverse else "\033[1;31m"
        return f"{decoration}{message}\033[0m"
    return message
