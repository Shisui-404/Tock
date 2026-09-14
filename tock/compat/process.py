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
