"""Optional Windows keep-awake so sleep does not kill long-running bot sessions."""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

logger = logging.getLogger(__name__)

# ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_AWAYMODE_REQUIRED = 0x00000040

_active = False


def keep_awake_enabled() -> bool:
    raw = os.getenv("WINDOWS_KEEP_AWAKE", "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


def request_keep_awake() -> bool:
    """Prevent system sleep while the bot/server is running (Windows only)."""
    global _active
    if not keep_awake_enabled() or sys.platform != "win32":
        return False
    try:
        import ctypes

        flags = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_AWAYMODE_REQUIRED
        result = ctypes.windll.kernel32.SetThreadExecutionState(flags)
        if not result:
            # Away-mode unsupported on some hosts — retry without it.
            flags = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
            result = ctypes.windll.kernel32.SetThreadExecutionState(flags)
        if result:
            _active = True
            logger.info("Windows keep-awake enabled (WINDOWS_KEEP_AWAKE)")
            return True
        logger.warning("SetThreadExecutionState failed; keep-awake not active")
    except Exception as exc:
        logger.warning("Could not enable keep-awake: %s", exc)
    return False


def release_keep_awake() -> None:
    """Allow the system to sleep again."""
    global _active
    if not _active or sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        _active = False
        logger.info("Windows keep-awake released")
    except Exception as exc:
        logger.warning("Could not release keep-awake: %s", exc)


def is_keep_awake_active() -> bool:
    return _active
