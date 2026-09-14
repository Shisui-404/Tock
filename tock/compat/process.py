"""Cross-platform detached process launch."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import IO


def spawn_detached(command: list[str], *, log: IO[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    """Start a process that outlives this terminal, with no stdin and output to ``log``."""
    options: dict[str, object] = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True  # no SIGHUP when the terminal closes
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                            cwd=cwd, env=env, close_fds=True, **options)


def process_alive(pid: int) -> bool:
    """True while ``pid`` is a running process; a finished child is reaped rather than reported alive."""
    if os.name == "nt":
        return _process_alive_windows(pid)
    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)
        return reaped == 0
    except ChildProcessError:  # not our child; probe it instead
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists but belongs to someone else
        return True
    return True


def _process_alive_windows(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    synchronize, query_limited_information = 0x00100000, 0x1000
    wait_timeout, access_denied = 0x102, 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(synchronize | query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == access_denied
    try:
        return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
    finally:
        kernel32.CloseHandle(handle)
