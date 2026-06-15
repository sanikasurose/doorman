"""macOS screen lock command abstraction. Knows nothing about FSM state."""

from __future__ import annotations

import logging
import subprocess
import time

logger = logging.getLogger("doorman.locker")

_LOCK_SCRIPT = (
    'tell application "System Events" to keystroke "q" '
    "using {control down, command down}"
)
_LOCKED_CHECK_SCRIPT = (
    'tell application "System Events" to '
    '(name of first process whose frontmost is true) is "loginwindow"'
)
_OSASCRIPT_TIMEOUT = 5  # seconds


class LockError(Exception):
    """Raised when the lock command fails after all retries."""


def is_screen_locked() -> bool:
    """Return True if the lock screen is currently active."""
    try:
        result = subprocess.run(
            ["osascript", "-e", _LOCKED_CHECK_SCRIPT],
            capture_output=True,
            text=True,
            timeout=_OSASCRIPT_TIMEOUT,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "true"
    except Exception as exc:
        # Fail open: if we can't determine state, attempt the lock anyway
        logger.warning("is_screen_locked() check failed: %s", exc)
        return False


def lock() -> bool:
    """Send the macOS lock command. Returns True on success, False on permanent failure.

    Skips if the screen is already locked. Retries once after 2 seconds on failure.
    """
    if is_screen_locked():
        logger.info("Screen already locked — skipping lock command")
        return True

    for attempt in (1, 2):
        try:
            result = subprocess.run(
                ["osascript", "-e", _LOCK_SCRIPT],
                capture_output=True,
                text=True,
                timeout=_OSASCRIPT_TIMEOUT,
            )
            if result.returncode == 0:
                logger.info("Screen locked successfully (attempt %d)", attempt)
                return True
            logger.warning(
                "Lock command failed (attempt %d): %s", attempt, result.stderr.strip()
            )
        except subprocess.TimeoutExpired:
            logger.warning("Lock command timed out (attempt %d)", attempt)
        except Exception as exc:
            logger.warning("Lock command raised unexpectedly (attempt %d): %s", attempt, exc)

        if attempt == 1:
            time.sleep(2)

    logger.error("Screen lock failed after 2 attempts")
    return False
