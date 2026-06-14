# Product Requirements Document

# Doorman — macOS Presence-Based Screen Locker

**Version:** 1.0  
**Author:** Sanika Surose  
**Status:** Final  
**Last Updated:** 2026-06-14

---

## 1. Overview

Doorman is a macOS background daemon that uses computer vision to detect user presence via the built-in FaceTime camera and automatically locks the screen when the user is no longer detected. It combines OpenCV and MediaPipe for real-time face detection, `face_recognition` for identity verification, and native macOS APIs for system integration. It runs as a launchd LaunchAgent, starting automatically on login, and exposes both a CLI and a menubar app for user control.

### Goals

- Automatically lock the screen when the user leaves their desk
- Only suppress locking when the _user's own face_ (or a trusted face) is detected
- Avoid false positives from active sessions, poor lighting, or brief absences
- Be observable, configurable, and well-structured enough to demonstrate production-quality engineering

### Non-Goals

- Unlocking the screen (macOS handles this natively)
- Supporting non-macOS platforms
- Remote or network-based access control
- Attention/gaze detection (deferred to v1.2)
- Presence-based power management beyond screen locking (deferred)

### Distribution

GitHub repository with a manual `install.sh` script. No app store, no Homebrew tap (v1).

---

## 2. Users

**Primary user:** The developer — a software engineering student running this daily on a MacBook Pro 2024.  
**Secondary users:** Anyone who clones the repo and runs the install script.

No multi-user account support required. Single-user macOS session assumed.

---

## 3. Tech Stack

| Component          | Choice                                     | Reason                                      |
| ------------------ | ------------------------------------------ | ------------------------------------------- |
| Language           | Python 3.11+                               | CV ecosystem, rapid iteration               |
| Face detection     | MediaPipe `FaceDetection`                  | Fast, lightweight, no landmarks needed      |
| Face recognition   | `face_recognition` (dlib)                  | Standard, well-documented identity matching |
| Camera access      | OpenCV `VideoCapture`                      | Cross-platform, simple API                  |
| Config             | TOML (`tomllib` stdlib)                    | Human-readable, stdlib in 3.11+             |
| CLI                | `typer`                                    | Clean API, auto-generates help text         |
| Menubar            | `rumps`                                    | Minimal macOS menubar library for Python    |
| System integration | `subprocess` + `osascript`                 | macOS lock, notifications, session state    |
| Idle time          | `IOKit` via `pyobjc`                       | HID idle time without external binaries     |
| Daemon             | launchd LaunchAgent                        | Native macOS process lifecycle management   |
| Logging            | Python `logging` (structured, file output) | Required for headless daemon observability  |
| Testing            | `pytest`                                   | Standard                                    |
| Packaging          | `pyproject.toml`                           | Modern Python packaging                     |

---

## 4. Feature Specification

### 4.1 Core Detection & Locking

**Face Detection Loop**

- Poll camera at configurable FPS (default: 5 FPS)
- Use MediaPipe `FaceDetection` solution (not FaceMesh — landmarks not needed at this stage)
- Detection confidence exposed as a configurable threshold (default: 0.6)
- Dynamic FPS scaling: reduce to 2 FPS when presence has been stable for >2 minutes; restore to configured FPS on any absence event

**Presence Smoothing**

- Use a rolling window rather than a hard timer to determine absence
- Configurable parameters: `window_size` (default: 20 frames) and `required_absent_frames` (default: 15 of 20)
- This prevents single dropped frames or brief occlusions from triggering the lock flow

**Absence Timer & Lock Flow**

- After the smoothing window registers absence, start a configurable countdown (default: 30 seconds)
- At T-10 seconds, send a macOS notification warning the user
- On timeout, send the lock command via osascript
- If face reappears at any point before locking, cancel the countdown and return to MONITORING

**Lock Command**

```
osascript -e 'tell application "System Events" to keystroke "q" using {control down, command down}'
```

Preferred over `pmset displaysleepnow` for reliability on modern macOS.

---

### 4.2 Session Intelligence

**Active Session Detection**
Before locking, check whether any of the following are true and suppress locking if so:

- A video call is active (detected by checking whether known conferencing app processes are running: Zoom, Teams, FaceTime, Google Meet via Chrome)
- The screensaver is currently running

Note: system-wide audio playback detection is out of scope for v1 due to macOS API limitations. Process-based video call detection is more reliable and covers the most important suppression case.

**Idle Input Grace Period**

- Read HID idle time via `IOKit` (`pyobjc`)
- If the user has interacted with the keyboard or mouse within the last N seconds (default: 60), suppress locking even if face is not detected
- Configurable via `input_grace_seconds` in config

**Camera Health Check**

- On startup, capture 10 test frames
- If >80% are empty or near-black (mean pixel value <10), log a `CAMERA_UNAVAILABLE` warning and enter a degraded state
- In degraded state: retry camera every 30 seconds on exponential backoff; do not lock based on absence
- Log clearly when camera becomes available again

**Pause Mode**

- Create/delete a flag file at `~/.doorman/pause` to suppress locking
- Daemon polls for this file each cycle
- Pause can have an optional expiry timestamp written into the file (used by the CLI `pause` command)
- In PAUSED state, poll at 1 FPS to reduce CPU usage

---

### 4.3 Face Recognition

**Enrollment**

- `doorman enroll-face --name <label>` CLI command
- Captures 20 frames and extracts a face encoding from each valid frame
- Stores all encodings individually per person (not averaged) to preserve variance across lighting conditions and angles
- Persists encodings to `~/.doorman/encodings.pkl` as a dict mapping label → list of encodings
- Handles: no face detected during enrollment, multiple faces detected (prompts user to ensure only one face is present), poor lighting warning if confidence is low

**Runtime Identity Matching**

- During MONITORING, compare each detected face against all stored encodings for each enrolled person using `face_recognition.compare_faces()`
- A person is considered recognised if any of their stored encodings match within the configured tolerance (default: 0.6)
- Only suppress locking if a known/trusted face matches
- Unknown face detected = treated as absence (no enrolled person present)

**Trusted Faces**

- Multiple people can be enrolled under different labels
- All enrolled faces are trusted by default
- `doorman list-faces` shows enrolled labels
- `doorman remove-face --name <label>` removes all encodings for that label

---

### 4.4 System & Configuration

**Config File**
Location: `~/.doorman/config.toml`  
Installed with defaults by `install.sh`. Never overwritten on reinstall without `--force`.

```toml
[detection]
fps = 5
confidence_threshold = 0.6
window_size = 20
required_absent_frames = 15
dynamic_fps_scaling = true

[locking]
absence_timeout_seconds = 30
warning_seconds_before_lock = 10

[session]
input_grace_seconds = 60
check_video_calls = true
check_screensaver = true

[recognition]
enabled = true
tolerance = 0.6

[logging]
level = "INFO"
log_file = "~/.doorman/doorman.log"
max_log_size_mb = 10
backup_count = 3
```

**Logging**

- Rotating file log at `~/.doorman/doorman.log`
- Structured log entries with timestamp, level, FSM state, and event type
- Key events logged: state transitions, lock triggered, lock cancelled, face enrolled, camera errors, config loaded, session suppression reasons

**launchd LaunchAgent**

- Plist at `~/Library/LaunchAgents/com.doorman.agent.plist`
- `RunAtLoad = true`, `KeepAlive = true`
- `ThrottleInterval = 10` (prevents crash loop hammering)
- stdout/stderr redirected to `~/.doorman/daemon.log`

**Install Script (`install.sh`)**

- Checks Python version (3.11+)
- Creates and activates virtual environment
- Installs dependencies from `pyproject.toml`
- Creates `~/.doorman/` directory structure
- Copies default config if not already present
- Installs and loads launchd plist
- Checks camera permission status and prints guidance if not granted
- Prints post-install summary

**Uninstall Script (`uninstall.sh`)**

- Unloads and removes launchd plist
- Optionally removes `~/.doorman/` (prompts user — non-destructive by default)

---

### 4.5 Interfaces

**CLI (`doorman`)**

| Command                              | Description                                                 |
| ------------------------------------ | ----------------------------------------------------------- |
| `doorman status`                     | Show current FSM state, FPS, config summary, last lock time |
| `doorman pause [--minutes N]`        | Pause locking (indefinitely or for N minutes)               |
| `doorman resume`                     | Cancel an active pause                                      |
| `doorman enroll-face --name <label>` | Enroll a new face                                           |
| `doorman list-faces`                 | List enrolled face labels                                   |
| `doorman remove-face --name <label>` | Remove an enrolled face                                     |
| `doorman stats`                      | Show lock statistics summary                                |
| `doorman set-timeout <seconds>`      | Update absence timeout in config                            |
| `doorman logs`                       | Tail the daemon log                                         |
| `doorman restart`                    | Restart the launchd agent                                   |
| `doorman debug`                      | Launch daemon in foreground with live OpenCV overlay        |

**Menubar App (`rumps`)**

- Icon indicates current state: active (green), paused (yellow), camera unavailable (red)
- Menu items: current status (non-interactive), separator, Pause / Resume toggle, Pause for 30 minutes, separator, Open Config, View Logs, separator, Quit
- Reads daemon state from a status file written by the daemon each cycle (`~/.doorman/status.json`)
- Does not directly control the daemon — writes to the pause flag file and reads the status file

**Debug Mode (`doorman debug`)**

- Launches the daemon in foreground as a typer subcommand
- Opens a live OpenCV window with overlay displaying:
  - Camera feed with face bounding box and confidence score
  - Current FSM state
  - Rolling window visualisation (present/absent frame indicators)
  - Countdown timer (when in WARNING state)
  - Identity match label (when face recognition is active)
  - Current FPS
- Does not write to the log file and does not load as a daemon

---

### 4.6 Observability

**Statistics Logging**

- Append-only JSONL file at `~/.doorman/stats.jsonl`
- Each lock event records: timestamp, time since last face detected, FSM state at lock, suppression reason if applicable
- `doorman stats` command reads this file and outputs:
  - Total locks today / this week / all time
  - Average absence duration before lock
  - Most common suppression reasons
  - Longest uninterrupted monitoring session

---

## 5. State Machine

```
INITIALIZING
    │
    ▼
CAMERA_UNAVAILABLE ◄──────────────────────────────┐
    │ (camera ok)                                  │ (camera lost)
    ▼                                              │
MONITORING ─────────────────────────────────────► ┘
    │ (absence window filled)
    ▼
WARNING (countdown active)
    │                    │
    │ (face returns)     │ (timeout)
    ▼                    ▼
MONITORING           LOCKED ──► MONITORING (macOS handles unlock; daemon continues)

PAUSED (from any state except LOCKED/INITIALIZING)
    │ (resume or expiry)
    ▼
MONITORING
```

**State written to `~/.doorman/status.json` each cycle for CLI and menubar to read.**

---

## 6. Edge Cases & Failure Handling

| Scenario                         | Handling                                                                                  |
| -------------------------------- | ----------------------------------------------------------------------------------------- |
| Camera in use by another app     | Detect empty frames on open; enter `CAMERA_UNAVAILABLE` with backoff retry                |
| Camera permission denied         | Detect on startup; log error and exit with clear message                                  |
| MacBook in clamshell mode        | Camera unavailable; enter `CAMERA_UNAVAILABLE`; rely on input grace period as fallback    |
| System sleep/wake                | launchd resumes daemon; re-run camera health check on wake                                |
| Multiple faces in frame          | Any enrolled face present = suppress lock                                                 |
| No faces enrolled                | Fall back to detection-only mode (any face suppresses lock); log warning on startup       |
| Config file missing or malformed | Exit with clear error message; do not use silent defaults                                 |
| Lock command fails               | Log error; retry once after 2 seconds; log failure if retry fails                         |
| Screen already locked            | Check display state before sending lock command; skip if already locked                   |
| Crash loop                       | launchd `ThrottleInterval` prevents tight restart loop; crash reason logged to daemon.log |
| Pause file left stale            | If pause file has an expiry timestamp, daemon clears it automatically on expiry           |

---

## 7. File & Directory Structure

```
doorman/                           # Repo root
├── doorman/                       # Main package
│   ├── __init__.py
│   ├── main.py                    # Entry point, arg parsing, daemon setup
│   ├── detector.py                # Camera + MediaPipe detection logic
│   ├── recognizer.py              # face_recognition identity matching
│   ├── state_machine.py           # FSM implementation
│   ├── locker.py                  # macOS lock command abstraction
│   ├── session_guard.py           # Active session checks + idle time
│   ├── notifier.py                # osascript notifications
│   ├── config.py                  # Config loading and validation
│   ├── stats.py                   # Statistics logging and reading
│   └── logger.py                  # Logging setup
├── cli/
│   └── cli.py                     # typer CLI entrypoint
├── menubar/
│   └── menubar.py                 # rumps menubar app
├── config/
│   └── default_config.toml        # Default config (copied by install.sh)
├── launchd/
│   └── com.doorman.agent.plist
├── tests/
│   ├── conftest.py
│   ├── test_state_machine.py
│   ├── test_detector.py           # Mock camera frames
│   ├── test_recognizer.py
│   ├── test_session_guard.py
│   └── fixtures/                  # Synthetic test video frames
├── scripts/
│   ├── install.sh
│   └── uninstall.sh
├── .claude/
│   ├── PRD.md                     # This document
│   ├── architecture.md            # Module boundaries and data flow
│   └── decisions.md               # Architectural decision records
├── CLAUDE.md                      # Current phase context for Claude Code
├── pyproject.toml
├── README.md
└── .gitignore
```

**Runtime directory (`~/.doorman/`, created by install.sh):**

```
~/.doorman/
├── config.toml          # User config
├── encodings.pkl        # Enrolled face encodings (dict: label → list of encodings)
├── status.json          # Current daemon state (written each cycle)
├── pause                # Pause flag file (present = paused)
├── doorman.log          # Rotating daemon log
├── daemon.log           # launchd stdout/stderr
└── stats.jsonl          # Append-only lock event statistics
```

---

## 8. Build Phases

| Phase | Days     | Deliverable                                                                 |
| ----- | -------- | --------------------------------------------------------------------------- |
| 1     | Day 1    | Scaffolding, basic camera + MediaPipe detection                             |
| 2     | Day 2    | State machine, config system, logging                                       |
| 3     | Day 3    | Presence smoothing, lock integration (first working end-to-end)             |
| 4     | Day 4    | Session intelligence (active session, idle input, health check, pause mode) |
| 5     | Days 5–6 | Face recognition (enrollment + runtime matching + trusted faces)            |
| 6     | Day 7    | launchd plist, install/uninstall scripts                                    |
| 7     | Day 8    | CLI interface                                                               |
| 8     | Day 9    | Menubar app                                                                 |
| 9     | Day 10   | Debug mode, statistics logging                                              |
| 10    | Day 11   | Tests, polish, documentation                                                |

---

## 9. Out of Scope (v1)

- Attention / gaze detection (v1.2)
- Homebrew distribution (v2)
- Multi-user macOS account support
- External webcam primary support
- Windows or Linux
- Webhook / event streaming
- Presence-based sleep/hibernate management
- Network-context-aware behaviour
- System-wide audio playback detection
