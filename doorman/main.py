"""Entry point for the Doorman daemon. Wires all modules and runs the detection loop."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from doorman.config import ConfigError, load_config
from doorman.detector import CameraUnavailableError, Detector
from doorman.logger import setup_logger
from doorman.state_machine import Action, StateMachine

_CONFIG_PATH = Path("~/.doorman/config.toml").expanduser()
_STATUS_FILE = Path("~/.doorman/status.json").expanduser()


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

    fsm = StateMachine(
        absence_timeout_seconds=config.locking.absence_timeout_seconds,
        warning_seconds_before_lock=config.locking.warning_seconds_before_lock,
        fps=config.detection.fps,
        recognition_enabled=config.recognition.enabled,
        status_file=_STATUS_FILE,
        window_size=config.detection.window_size,
        required_absent_frames=config.detection.required_absent_frames,
    )

    sleep_interval = 1.0 / config.detection.fps
    logger.info("Detection loop starting at %d FPS", config.detection.fps)

    try:
        while True:
            try:
                detection = detector.detect()
            except Exception as exc:
                logger.warning("detect() raised unexpectedly: %s", exc)
                detection = None

            # Recognition and session guard are None until their phases are built.
            state, action = fsm.tick(
                detection=detection,
                recognition=None,
                session=None,
                pause=False,
            )

            if action == Action.SEND_LOCK:
                logger.info("Action: SEND_LOCK (not yet wired — Day 3)")
            elif action == Action.SEND_WARNING:
                logger.info("Action: SEND_WARNING (not yet wired — Day 3)")
            elif action == Action.CANCEL_WARNING:
                logger.info("Action: CANCEL_WARNING")

            logger.debug(
                "state=%s faces=%s confidence=%.2f",
                state.name,
                getattr(detection, "faces_found", None),
                getattr(detection, "confidence", 0.0),
            )

            time.sleep(sleep_interval)
    except KeyboardInterrupt:
        logger.info("Shutting down on KeyboardInterrupt")
    finally:
        detector.release()
        logger.info("Doorman stopped")


if __name__ == "__main__":
    main()
