"""Small boundary for platform-specific behavior."""

from __future__ import annotations

import platform
import sys

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
IS_WSL = IS_LINUX and "microsoft" in platform.release().lower()
