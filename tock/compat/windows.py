"""Reaching Windows from native Windows or from WSL through PowerShell."""

from __future__ import annotations

import base64
import functools
import shutil
import subprocess
import sys
from pathlib import Path


@functools.cache
def is_wsl() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def powershell() -> str | None:
    """PowerShell on native Windows or through WSL interop, if reachable."""
    if sys.platform != "win32" and not is_wsl():
        return None
    return shutil.which("powershell.exe")


def powershell_command(executable: str, script: str) -> list[str]:
    # -EncodedCommand (UTF-16LE base64) sidesteps every shell and WSL quoting rule.
    encoded = base64.b64encode(("$ProgressPreference = 'SilentlyContinue'\n$ErrorActionPreference = 'Stop'\n" + script).encode("utf-16-le")).decode("ascii")
    return [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]


def ps_quote(text: str) -> str:
    """A PowerShell single-quoted literal, which performs no interpolation."""
    return "'" + text.replace("'", "''") + "'"


def windows_path(path: Path) -> str | None:
    if sys.platform == "win32":
        return str(path)
    try:
        result = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
