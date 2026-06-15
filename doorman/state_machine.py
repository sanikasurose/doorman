"""FSM for presence detection states. Zero imports from other doorman modules (ADR-013)."""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path

logger = logging.getLogger("doorman.state_machine")


class State(Enum):
    INITIALIZING = auto()
    CAMERA_UNAVAILABLE = auto()
    MONITORING = auto()
    WARNING = auto()
    LOCKED = auto()
    PAUSED = auto()


class Action(Enum):
    NO_OP = auto()
    SEND_WARNING = auto()
    SEND_LOCK = auto()
    CANCEL_WARNING = auto()


class StateMachine:
    def __init__(
        self,
        absence_timeout_seconds: int,
        warning_seconds_before_lock: int,
        fps: int,
        recognition_enabled: bool,
        status_file: Path,
        window_size: int,
        required_absent_frames: int,
    ) -> None:
        self._absence_timeout = absence_timeout_seconds
        self._warning_before_lock = warning_seconds_before_lock
        self._fps = fps
        self._recognition_enabled = recognition_enabled
        self._status_file = status_file
        self._required_absent_frames = required_absent_frames

        self._presence_window: deque[bool] = deque(maxlen=window_size)

        self._state = State.INITIALIZING
        self._warning_entered_at: datetime | None = None
        self._warning_sent = False
        self._last_lock: datetime | None = None
        self._last_face_seen: datetime | None = None

        self._write_status()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def tick(self, detection, recognition, session, pause: bool) -> tuple[State, Action]:
        """Advance the FSM one cycle. detection/recognition/session may be None."""
        if detection is not None and getattr(detection, "faces_found", False):
            self._last_face_seen = datetime.now(timezone.utc)

        prev = self._state
        next_state, action = self._transition(detection, recognition, session, pause)
        self._state = next_state

        if next_state != prev:
            logger.info("FSM transition: %s → %s", prev.name, next_state.name)
            self._write_status()

        return next_state, action

    @property
    def state(self) -> State:
        return self._state

    # ------------------------------------------------------------------
    # Transition logic
    # ------------------------------------------------------------------

    def _transition(self, detection, recognition, session, pause: bool) -> tuple[State, Action]:
        camera_ok = detection is not None
        faces_found = camera_ok and getattr(detection, "faces_found", False)
        suppressed = session is not None and getattr(session, "suppress", False)
        now = datetime.now(timezone.utc)

        # Pause is honoured from any state except INITIALIZING and LOCKED
        if pause and self._state not in (State.INITIALIZING, State.LOCKED):
            return State.PAUSED, Action.NO_OP

        match self._state:
            case State.INITIALIZING:
                if not camera_ok:
                    return State.CAMERA_UNAVAILABLE, Action.NO_OP
                return State.MONITORING, Action.NO_OP

            case State.CAMERA_UNAVAILABLE:
                if camera_ok:
                    return State.MONITORING, Action.NO_OP
                return State.CAMERA_UNAVAILABLE, Action.NO_OP

            case State.MONITORING:
                if not camera_ok:
                    return State.CAMERA_UNAVAILABLE, Action.NO_OP
                # Session suppression counts as presence — don't start countdown.
                if suppressed or faces_found:
                    self._presence_window.append(True)
                    return State.MONITORING, Action.NO_OP
                self._presence_window.append(False)
                absent_count = self._presence_window.count(False)
                if absent_count < self._required_absent_frames:
                    return State.MONITORING, Action.NO_OP
                # Rolling window threshold reached — start countdown
                self._warning_entered_at = now
                self._warning_sent = False
                return State.WARNING, Action.NO_OP

            case State.WARNING:
                if not camera_ok:
                    self._warning_entered_at = None
                    return State.CAMERA_UNAVAILABLE, Action.CANCEL_WARNING
                # Face returned or session suppression: cancel countdown.
                if faces_found or suppressed:
                    self._warning_entered_at = None
                    self._warning_sent = False
                    self._presence_window.clear()
                    return State.MONITORING, Action.CANCEL_WARNING

                elapsed = (
                    (now - self._warning_entered_at).total_seconds()
                    if self._warning_entered_at
                    else 0.0
                )

                if elapsed >= self._absence_timeout:
                    self._last_lock = now
                    self._warning_entered_at = None
                    self._warning_sent = False
                    return State.LOCKED, Action.SEND_LOCK

                time_remaining = self._absence_timeout - elapsed
                if time_remaining <= self._warning_before_lock and not self._warning_sent:
                    self._warning_sent = True
                    return State.WARNING, Action.SEND_WARNING

                return State.WARNING, Action.NO_OP

            case State.LOCKED:
                # Lock command was fired; daemon immediately resumes monitoring.
                # macOS owns the unlock flow.
                return State.MONITORING, Action.NO_OP

            case State.PAUSED:
                if not pause:
                    return State.MONITORING, Action.NO_OP
                return State.PAUSED, Action.NO_OP

        return self._state, Action.NO_OP  # unreachable

    # ------------------------------------------------------------------
    # Status file
    # ------------------------------------------------------------------

    def _write_status(self) -> None:
        """Atomically write ~/.doorman/status.json."""
        self._status_file.parent.mkdir(parents=True, exist_ok=True)

        warning_countdown: int | None = None
        if self._state == State.WARNING and self._warning_entered_at:
            elapsed = (datetime.now(timezone.utc) - self._warning_entered_at).total_seconds()
            warning_countdown = max(0, int(self._absence_timeout - elapsed))

        payload = {
            "state": self._state.name,
            "fps": self._fps,
            "last_lock": self._last_lock.isoformat() if self._last_lock else None,
            "last_face_seen": self._last_face_seen.isoformat() if self._last_face_seen else None,
            "warning_countdown": warning_countdown,
            "camera_available": self._state != State.CAMERA_UNAVAILABLE,
            "recognition_enabled": self._recognition_enabled,
            "matched_label": None,
        }

        tmp = self._status_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self._status_file)
