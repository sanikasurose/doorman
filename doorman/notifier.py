"""macOS notification sending via osascript. Knows nothing about FSM state."""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger("doorman.notifier")

_APP_NAME = "Doorman"
_OSASCRIPT_TIMEOUT = 5  # seconds


def _notify(message: str, title: str = _APP_NAME) -> None:
    script = f'display notification "{message}" with title "{title}"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=_OSASCRIPT_TIMEOUT,
        )
    except Exception as exc:
        logger.warning("Notification failed: %s", exc)


def send_warning(seconds_remaining: int) -> None:
    """Send a macOS notification warning that the screen will lock soon."""
    logger.info("Sending lock warning: %ds remaining", seconds_remaining)
    _notify(f"Screen will lock in {seconds_remaining} seconds.")


def send_cancelled() -> None:
    """Send a macOS notification that the pending lock was cancelled."""
    logger.info("Sending lock cancelled notification")
    _notify("Screen lock cancelled — welcome back.")
