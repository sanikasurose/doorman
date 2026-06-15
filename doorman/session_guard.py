"""Active session checks (video calls, screensaver, HID idle time) that suppress locking."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

logger = logging.getLogger("doorman.session_guard")

# Process names to match against (lowercased). pgrep is case-insensitive by default on macOS.
_VIDEO_CALL_PROCESSES = ["zoom.us", "Microsoft Teams", "FaceTime"]


@dataclass
class SessionGuardResult:
    suppress: bool
    reason: str | None


class SessionGuard:
    """Checks active session conditions and returns whether locking should be suppressed."""

    def __init__(self, config) -> None:
        self._config = config

    def check(self) -> SessionGuardResult:
        """Return suppress=True with the first matching reason in priority order."""
        # input_grace is always checked — not togglable.
        idle = _hid_idle_seconds()
        if idle is not None and idle < self._config.session.input_grace_seconds:
            return SessionGuardResult(suppress=True, reason="input_grace")

        if self._config.session.check_video_calls and _video_call_active():
            return SessionGuardResult(suppress=True, reason="video_call")

        if self._config.session.check_screensaver and _screensaver_active():
            return SessionGuardResult(suppress=True, reason="screensaver")

        return SessionGuardResult(suppress=False, reason=None)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _hid_idle_seconds() -> float | None:
    """Return seconds since last user HID event via IOKit IOHIDSystem (nanoseconds → seconds)."""
    try:
        result = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        for line in result.stdout.splitlines():
            if "HIDIdleTime" in line:
                ns = int(line.split("=")[-1].strip())
                return ns / 1e9
        logger.warning("HIDIdleTime not found in ioreg output")
        return None
    except Exception as exc:
        logger.warning("HID idle time unavailable: %s", exc)
        return None


def _video_call_active() -> bool:
    """Return True if any known conferencing app process is running via pgrep."""
    for name in _VIDEO_CALL_PROCESSES:
        try:
            result = subprocess.run(
                ["pgrep", "-ix", name],
                capture_output=True,
                timeout=2,
            )
            if result.returncode == 0:
                return True
        except Exception as exc:
            logger.warning("pgrep check for '%s' failed: %s", name, exc)

    # Google Meet: only suppress if Chrome has a meet.google.com window open,
    # not whenever Chrome is running.
    try:
        result = subprocess.run(
            ["pgrep", "-if", "meet.google.com"],
            capture_output=True,
            timeout=2,
        )
        if result.returncode == 0:
            return True
    except Exception as exc:
        logger.warning("pgrep check for Google Meet failed: %s", exc)

    return False


def _screensaver_active() -> bool:
    """Return True if the macOS screensaver is currently running."""
    try:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get running of screen saver preferences',
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip() == "true"
    except Exception as exc:
        logger.warning("Screensaver check failed: %s", exc)
        return False
