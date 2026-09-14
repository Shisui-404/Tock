"""Advisory exclusive file locks for POSIX and Windows."""

from __future__ import annotations

import os
import time
from pathlib import Path


class LockUnavailableError(RuntimeError):
    """Raised when a non-blocking lock is already held."""


class FileLock:
    """A cross-platform exclusive lock whose lifetime is this context manager."""

    def __init__(self, path: Path, *, blocking: bool = True) -> None:
        self.path = path
        self.blocking = blocking
        self._file = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                self._lock_windows()
            else:
                self._lock_posix()
        except Exception:
            self._file.close()
            self._file = None
            raise
        return self

    def _lock_posix(self) -> None:
        import fcntl

        flags = fcntl.LOCK_EX | (0 if self.blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(self._file.fileno(), flags)
        except BlockingIOError as error:
            raise LockUnavailableError(f"lock is held: {self.path}") from error

    def _lock_windows(self) -> None:
        import msvcrt

        self._file.seek(0)
        if self._file.tell() == 0:
            self._file.write(" ")
            self._file.flush()
        mode = msvcrt.LK_LOCK if self.blocking else msvcrt.LK_NBLCK
        while True:
            try:
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), mode, 1)
                return
            except OSError as error:
                if not self.blocking:
                    raise LockUnavailableError(f"lock is held: {self.path}") from error
                time.sleep(0.05)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None
