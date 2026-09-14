"""Non-blocking line input without terminal raw mode."""

from __future__ import annotations

import os
import sys


class LinePoller:
    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stdin
        self._buffer = ""

    @property
    def available(self) -> bool:
        return self.stream.isatty()

    def poll(self) -> str | None:
        """Return a completed line if available, otherwise ``None``."""
        if not self.available:
            return None
        if os.name == "nt":
            return self._poll_windows()
        return self._poll_posix()

    def _poll_windows(self) -> str | None:
        import msvcrt
        while msvcrt.kbhit():
            char = msvcrt.getwch()
            if char in ("\r", "\n"):
                value, self._buffer = self._buffer, ""
                return value
            if char == "\b":
                self._buffer = self._buffer[:-1]
            elif char.isprintable():
                self._buffer += char
        return None

    def _poll_posix(self) -> str | None:
        import select
        readable, _, _ = select.select([self.stream], [], [], 0)
        return self.stream.readline().rstrip("\r\n") if readable else None
