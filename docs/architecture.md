# Doorman — Architecture

This document defines module boundaries, data flow, and inter-process communication for the Doorman daemon. Refer to the PRD for feature-level requirements and the decisions log for why key choices were made.

---

## System Overview

Doorman has three runtime processes:

1. **Daemon** (`doorman/`) — the long-running background process. Owns the camera, runs the detection loop, manages the FSM, and writes shared state to disk.
2. **CLI** (`cli/cli.py`) — a short-lived typer process. Reads shared state files and writes control files (pause flag, config). Never talks to the daemon directly.
3. **Menubar** (`menubar/menubar.py`) — a persistent rumps process. Reads shared state files on a timer. Writes to the pause flag file only.

These three processes communicate exclusively through files in `~/.doorman/`. There is no socket, pipe, or shared memory. This keeps the IPC layer simple and inspectable.

```
┌─────────────────────────────────────┐
│              Daemon                 │
│  detector → recognizer → FSM        │
│  session_guard ──────────┘          │
│                  │                  │
│            locker / notifier        │
│                  │                  │
│         writes ~/.doorman/          │
│         status.json, stats.jsonl    │
│         doorman.log                 │
└──────────────┬──────────────────────┘
               │ shared files
       ┌───────┴────────┐
       ▼                ▼
┌─────────┐      ┌────────────┐
│   CLI   │      │  Menubar   │
│ reads   │      │ reads      │
│ status  │      │ status     │
│ writes  │      │ writes     │
│ pause   │      │ pause      │
│ config  │      └────────────┘
└─────────┘
```

---

## Module Responsibilities

Each module has a single owner and a strict boundary. No module reaches into another module's domain.

### `doorman/main.py`

- Entry point for the daemon process
- Parses CLI args passed through by typer (`--config`, `--debug`)
- Initialises all modules in the correct order: config → logger → detector → recognizer → session_guard → state_machine
- Runs the main polling loop
- Handles SIGTERM/SIGINT for clean shutdown
- Does NOT contain any detection, locking, or FSM logic

### `doorman/config.py`

- Loads and validates `~/.doorman/config.toml` using `tomllib`
- Exposes a single `Config` dataclass consumed by all other modules
- Raises a clear `ConfigError` on missing or malformed config — never falls back to silent defaults
- Does NOT write config (that is the CLI's job via `doorman set-timeout` etc.)

### `doorman/logger.py`

- Sets up the rotating file logger at `~/.doorman/doorman.log`
- In debug mode, logs to stdout instead
- All other modules import the logger from here — no module configures its own logging

### `doorman/detector.py`

- Owns the OpenCV `VideoCapture` instance and the MediaPipe `FaceDetection` model
- Exposes a single method: `detect() → DetectionResult`
- `DetectionResult` contains: `faces_found: bool`, `confidence: float`, `bounding_boxes: list`
- Performs the camera health check on initialisation (10 test frames)
- Handles empty/black frame detection and raises `CameraUnavailableError`
- Does NOT know about identity, FSM state, or locking

### `doorman/recognizer.py`

- Loads `~/.doorman/encodings.pkl` on initialisation
- Exposes `identify(frame) → RecognitionResult`
- `RecognitionResult` contains: `known_face_present: bool`, `matched_label: str | None`
- Uses `face_recognition.compare_faces()` against all stored encodings for each enrolled person
- If no encodings file exists, returns `known_face_present=True` always (detection-only fallback mode) and logs a warning
- Does NOT touch the camera directly — receives frames from `detector.py`
- Handles enrollment: `enroll(label, frames) → None` — extracts encodings and persists to disk

### `doorman/state_machine.py`

- The FSM. Owns all state transition logic.
- States: `INITIALIZING`, `CAMERA_UNAVAILABLE`, `MONITORING`, `WARNING`, `LOCKED`, `PAUSED`
- Accepts inputs each cycle: `DetectionResult`, `RecognitionResult`, `session_guard_result`, `pause_flag_present`
- Returns the next state and any actions to take (`SEND_WARNING`, `SEND_LOCK`, `CANCEL_WARNING`, `NO_OP`)
- Does NOT call any system APIs itself — tells `main.py` what actions to take, which then delegates to `locker.py` and `notifier.py`
- Writes `~/.doorman/status.json` on every state transition

### `doorman/session_guard.py`

- Checks active session conditions that should suppress locking
- Exposes `check() → SessionGuardResult` with fields: `suppress: bool`, `reason: str | None`
- Checks: known video call processes running, screensaver active, HID idle time within grace period
- Each check is independently togglable via config
- Does NOT know about the FSM or camera

### `doorman/locker.py`

- Abstracts the macOS lock command
- Exposes `lock() → bool` (returns False if lock command fails)
- Checks display state before locking — skips and logs if screen is already locked
- Retries once on failure with a 2-second delay
- Does NOT know about FSM state — it just locks when told to

### `doorman/notifier.py`

- Sends macOS notifications via `osascript`
- Exposes `send_warning(seconds_remaining: int)` and `send_cancelled()`
- Does NOT know about FSM state

### `doorman/stats.py`

- Appends lock events to `~/.doorman/stats.jsonl`
- Exposes `record_lock(event: LockEvent)` and `read_stats() → StatsSummary`
- `StatsSummary` is consumed by the CLI `doorman stats` command
- Does NOT interact with the FSM directly — called by `main.py` when a lock occurs

---

## CLI Module (`cli/cli.py`)

- Built with `typer`
- Each subcommand is a short-lived process — reads state files, writes control files, exits
- `doorman debug` is the only command that starts a long-running process (calls `main.py` with debug flag)
- Commands that modify config (`set-timeout`) write directly to `~/.doorman/config.toml` using `tomllib` + `tomli-w`
- Commands that control the daemon (`pause`, `resume`, `restart`) write/delete the pause flag file or call `launchctl`
- Does NOT import from `doorman/` package directly except for `config.py` and `stats.py`

---

## Menubar Module (`menubar/menubar.py`)

- Built with `rumps`
- Polls `~/.doorman/status.json` every 5 seconds to update the icon and status label
- Pause/Resume writes to `~/.doorman/pause` flag file
- Runs as a separate process launched by the user (not by launchd)
- Does NOT import from `doorman/` package

---

## Shared File Contracts

These files are the IPC layer. Their format is a contract between the daemon, CLI, and menubar.

### `~/.doorman/status.json`

Written by the daemon on every state transition. Read by CLI and menubar.

```json
{
  "state": "MONITORING",
  "fps": 5,
  "last_lock": "2026-06-14T10:32:01",
  "last_face_seen": "2026-06-14T11:45:22",
  "warning_countdown": null,
  "camera_available": true,
  "recognition_enabled": true,
  "matched_label": "sanika"
}
```

### `~/.doorman/pause`

Presence of this file = daemon is paused. Written by CLI and menubar. Deleted by CLI, menubar, or daemon (on expiry).

```
2026-06-14T12:15:00
```

Content is an ISO timestamp expiry (optional). Empty file = indefinite pause.

### `~/.doorman/stats.jsonl`

Append-only. One JSON object per line, one per lock event.

```json
{
  "timestamp": "2026-06-14T10:32:01",
  "absence_duration_seconds": 47,
  "state_at_lock": "WARNING",
  "suppression_reason": null
}
```

---

## Main Loop Sequence (per cycle)

```
1. Check pause flag file → if present and not expired, enter/stay PAUSED
2. detector.detect() → DetectionResult
3. If CameraUnavailableError → transition to CAMERA_UNAVAILABLE, backoff retry
4. recognizer.identify(frame) → RecognitionResult
5. session_guard.check() → SessionGuardResult
6. state_machine.tick(detection, recognition, session, pause) → (next_state, actions)
7. Execute actions:
     SEND_WARNING → notifier.send_warning()
     SEND_LOCK    → locker.lock(), stats.record_lock()
     CANCEL       → notifier.send_cancelled()
8. Write status.json if state changed
9. Sleep until next cycle (1 / fps seconds)
```

---

## Data Flow Diagram

```
Camera (OpenCV)
    │
    ▼
detector.py ──────────────────────────────────────────┐
    │ DetectionResult                                  │ raw frame
    ▼                                                  ▼
recognizer.py                                   (debug overlay)
    │ RecognitionResult
    │
    ├──────────────────────┐
    ▼                      ▼
session_guard.py      state_machine.py
    │                      │
    └──────────────────────┘
                           │ actions
              ┌────────────┼────────────┐
              ▼            ▼            ▼
          locker.py  notifier.py   stats.py
              │
          osascript
```

---

## Dependency Rules

- `state_machine.py` has zero imports from any other doorman module
- `detector.py` and `recognizer.py` do not import from each other
- `locker.py` and `notifier.py` do not import from any other doorman module
- `main.py` is the only module that imports from all others
- `cli/cli.py` imports only from `config.py` and `stats.py`
- `menubar/menubar.py` imports nothing from the `doorman/` package
