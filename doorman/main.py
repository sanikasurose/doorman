"""Entry point for the Doorman daemon. Wires all modules and runs the detection loop."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from doorman.config import ConfigError, load_config
from doorman.detector import CameraUnavailableError, Detector
from doorman.logger import setup_logger
from doorman.locker import lock
from doorman.notifier import send_cancelled, send_warning
from doorman.session_guard import SessionGuard
from doorman.state_machine import Action, State, StateMachine

_CONFIG_PATH = Path("~/.doorman/config.toml").expanduser()
_STATUS_FILE = Path("~/.doorman/status.json").expanduser()
_PAUSE_FILE = Path("~/.doorman/pause").expanduser()

_SCALED_FPS = 2              # reduced poll rate after stable presence
_SCALE_AFTER_SECONDS = 120   # 2 minutes of continuous MONITORING before scaling

_CAMERA_BACKOFF_INITIAL = 30.0   # seconds between first retry attempts
_CAMERA_BACKOFF_MAX = 300.0      # cap at 5 minutes


def _check_pause() -> bool:
    """Read the pause flag file and return whether the daemon should be paused."""
    if not _PAUSE_FILE.exists():
        return False
    content = _PAUSE_FILE.read_text().strip()
    if not content:
        return True  # empty file = indefinite pause
    try:
        expiry = datetime.fromisoformat(content)
        if datetime.now(timezone.utc) >= expiry.astimezone(timezone.utc):
            _PAUSE_FILE.unlink(missing_ok=True)
            return False
        return True
    except ValueError:
        logger.warning("Pause file contains malformed timestamp %r — treating as indefinite pause", content)
        return True


def main() -> None:
    try:
        config = load_config(_CONFIG_PATH)
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    log_file = Path(config.logging.log_file).expanduser()
    debug_mode = config.logging.level.upper() == "DEBUG"
    logger = setup_logger(
        debug_mode=debug_mode,
        log_file=log_file,
        max_bytes=config.logging.max_log_size_mb * 1024 * 1024,
        backup_count=config.logging.backup_count,
    )
    logger.info("Doorman starting — config loaded from %s", _CONFIG_PATH)

    try:
        detector = Detector(
            fps=config.detection.fps,
            confidence_threshold=config.detection.confidence_threshold,
        )
    except CameraUnavailableError as e:
        logger.error("Camera unavailable on startup: %s", e)
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    session_guard = SessionGuard(config)

    fsm = StateMachine(
        absence_timeout_seconds=config.locking.absence_timeout_seconds,
        warning_seconds_before_lock=config.locking.warning_seconds_before_lock,
        fps=config.detection.fps,
        recognition_enabled=config.recognition.enabled,
        status_file=_STATUS_FILE,
        window_size=config.detection.window_size,
        required_absent_frames=config.detection.required_absent_frames,
    )

    configured_interval = 1.0 / config.detection.fps
    scaled_interval = 1.0 / _SCALED_FPS
    sleep_interval = configured_interval

    monitoring_entered_at: float | None = None
    prev_state: State | None = None

    # Camera backoff state
    camera_next_retry_at: float = 0.0
    camera_retry_interval: float = _CAMERA_BACKOFF_INITIAL

    logger.info("Detection loop starting at %d FPS", config.detection.fps)

    try:
        while True:
            pause = _check_pause()

            # --- Camera backoff retry when unavailable ---
            state = fsm.state
            if state == State.CAMERA_UNAVAILABLE:
                now_mono = time.monotonic()
                if now_mono >= camera_next_retry_at:
                    if detector.retry_camera():
                        logger.info("Camera recovered after retry")
                        camera_retry_interval = _CAMERA_BACKOFF_INITIAL
                        camera_next_retry_at = 0.0
                    else:
                        logger.info(
                            "Camera retry failed — next attempt in %.0fs",
                            camera_retry_interval,
                        )
                        camera_next_retry_at = now_mono + camera_retry_interval
                        camera_retry_interval = min(
                            camera_retry_interval * 2, _CAMERA_BACKOFF_MAX
                        )

            # --- Detection ---
            detection = None
            if state != State.CAMERA_UNAVAILABLE or detector.is_available():
                try:
                    detection = detector.detect()
                except Exception as exc:
                    logger.warning("detect() raised unexpectedly: %s", exc)

            # --- Session guard ---
            guard = session_guard.check()
            if guard.suppress:
                logger.debug("Suppressing lock — reason: %s", guard.reason)

            # --- FSM tick ---
            state, action = fsm.tick(
                detection=detection,
                recognition=None,
                session=guard,
                pause=pause,
            )

            # --- Action dispatch ---
            if action == Action.SEND_WARNING:
                send_warning(config.locking.warning_seconds_before_lock)
            elif action == Action.SEND_LOCK:
                success = lock()
                logger.info(
                    "Lock event recorded (stats not yet wired — Day 10), success=%s", success
                )
            elif action == Action.CANCEL_WARNING:
                send_cancelled()

            logger.debug(
                "state=%s faces=%s confidence=%.2f suppress=%s",
                state.name,
                getattr(detection, "faces_found", None),
                getattr(detection, "confidence", 0.0),
                guard.suppress,
            )

            # --- Dynamic FPS scaling ---
            if state == State.MONITORING:
                if prev_state != State.MONITORING:
                    monitoring_entered_at = time.monotonic()
                stable_for = time.monotonic() - (monitoring_entered_at or time.monotonic())
                sleep_interval = (
                    scaled_interval if stable_for >= _SCALE_AFTER_SECONDS else configured_interval
                )
            elif state == State.PAUSED:
                sleep_interval = 1.0  # 1 FPS in PAUSED state
            else:
                monitoring_entered_at = None
                sleep_interval = configured_interval

            prev_state = state
            time.sleep(sleep_interval)

    except KeyboardInterrupt:
        logger.info("Shutting down on KeyboardInterrupt")
    finally:
        detector.release()
        logger.info("Doorman stopped")


if __name__ == "__main__":
    main()
