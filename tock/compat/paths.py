"""Cross-platform data-directory selection."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_directory() -> Path:
    """Return the application data directory, creating it if necessary."""
    override = os.environ.get("TOCK_HOME")
    if override:
        path = Path(override).expanduser()
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "tock"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "tock"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "tock"
    path.mkdir(parents=True, exist_ok=True)
    return path
