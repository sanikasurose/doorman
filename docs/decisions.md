# Doorman — Architectural Decision Records

Decisions made during planning that Claude Code should treat as settled. Do not relitigate these mid-session. If a decision seems wrong in context, flag it for review rather than silently overriding it.

---

## ADR-001: MediaPipe FaceDetection over FaceMesh

**Decision:** Use `mediapipe.solutions.face_detection`, not `face_mesh`.

**Reason:** FaceMesh returns 468 landmarks and is designed for facial geometry tasks (AR filters, gaze estimation). Doorman only needs to know whether a face is present and its bounding box. FaceDetection is faster, uses less CPU, and is the correct tool for presence detection. FaceMesh is deferred to v1.2 if attention detection is added.

---

## ADR-002: Rolling window smoothing over hard timer

**Decision:** Absence is determined by a rolling window of frames (`required_absent_frames` of `window_size`), not by a simple timer reset on each detection.

**Reason:** A hard timer resets to zero on every positive detection, which means a single detected frame during a genuine absence resets the entire countdown. The rolling window is more robust to single dropped frames and brief occlusions without being fooled by intermittent detections during a real absence.

---

## ADR-003: Store multiple encodings per person, not an average

**Decision:** `encodings.pkl` stores a `dict[str, list[np.ndarray]]` — all 20 raw encodings per enrolled person.

**Reason:** Averaging encodings into a single vector loses variance. A person looks different under different lighting conditions and angles. Storing all encodings and comparing against each one individually with `compare_faces()` is more robust to real-world variation. The performance cost at 5 FPS is negligible.

---

## ADR-004: Flag file for pause IPC

**Decision:** Pause state is communicated via the presence of `~/.doorman/pause`, not via a socket, pipe, or signal.

**Reason:** File-based IPC is inspectable, requires no persistent connection, and survives daemon restarts. The CLI and menubar can write to it without knowing whether the daemon is currently running. A socket would require the daemon to be up and listening. SIGSTOP/SIGCONT would pause the entire process including launchd keepalive logic.

---

## ADR-005: status.json for daemon state sharing

**Decision:** The daemon writes its current state to `~/.doorman/status.json` on every transition. The CLI and menubar read this file; they do not query the daemon directly.

**Reason:** Same rationale as ADR-004. Polling a file is simpler and more resilient than maintaining an IPC connection. The CLI is a short-lived process; it cannot hold a socket open. The menubar polls every 5 seconds, so a 5-second staleness window is acceptable.

---

## ADR-006: TOML over YAML for config

**Decision:** Config is `~/.doorman/config.toml`, parsed with Python 3.11 stdlib `tomllib`.

**Reason:** `tomllib` is in stdlib from Python 3.11+, so no extra dependency. TOML is unambiguous (YAML has well-known parsing gotchas with booleans and numbers). TOML is increasingly the standard for Python tooling config.

---

## ADR-007: LaunchAgent over LaunchDaemon

**Decision:** Doorman runs as a LaunchAgent (`~/Library/LaunchAgents/`), not a LaunchDaemon (`/Library/LaunchDaemons/`).

**Reason:** LaunchAgents run in the user's GUI session, which is required for camera access, display state checks, and osascript. LaunchDaemons run as root before any user session exists and cannot access user-level GUI APIs. There is no reason to run with elevated privileges.

---

## ADR-008: osascript lock command over pmset

**Decision:** Lock via:
```
osascript -e 'tell application "System Events" to keystroke "q" using {control down, command down}'
```
Not `pmset displaysleepnow`.

**Reason:** `pmset displaysleepnow` puts the display to sleep but does not invoke the lock screen on all macOS versions and configurations. The keystroke approach triggers the same code path as the user pressing the lock shortcut manually and is reliable on macOS 13+.

---

## ADR-009: `doorman debug` as a subcommand, not a `--debug` flag

**Decision:** Debug mode is invoked as `doorman debug`, not `doorman --debug`.

**Reason:** `typer` handles top-level flags awkwardly when they change the fundamental behaviour of the process (foreground vs daemon, OpenCV window vs no window). A subcommand is a cleaner API — it is unambiguous, auto-documented in `doorman --help`, and easier to implement cleanly.

---

## ADR-010: Process-based video call detection

**Decision:** Video call detection checks whether known conferencing app processes are running (`Zoom`, `Teams`, `FaceTime`, `Google Meet` via Chrome). System-wide audio playback detection is out of scope for v1.

**Reason:** macOS does not expose a simple API for querying which process is playing audio without entitlements that a non-App-Store app cannot easily obtain. Process checking via `pgrep` or `psutil` is reliable, requires no special permissions, and covers the most important suppression case (video calls). Audio detection can be revisited in v1.1.

---

## ADR-011: typer over click for CLI

**Decision:** CLI is built with `typer`.

**Reason:** `typer` generates help text, argument validation, and shell completions from Python type annotations with less boilerplate than `click`. Both are acceptable; `typer` is the more modern choice and is built on top of `click` anyway.

---

## ADR-012: JSONL for statistics

**Decision:** Lock events are stored as append-only JSONL at `~/.doorman/stats.jsonl`.

**Reason:** JSONL (one JSON object per line) is append-only by nature, trivially parseable with a line-by-line read, human-inspectable, and requires no database dependency. A SQLite database would be overkill for the volume of events expected (at most a few dozen per day).

---

## ADR-013: Dependency injection via main.py

**Decision:** `main.py` instantiates all modules and passes them where needed. Modules do not instantiate each other.

**Reason:** Keeps modules independently testable. `test_state_machine.py` can instantiate `StateMachine` directly with no camera or macOS dependencies. `test_detector.py` can mock the camera at the OpenCV level without touching the FSM. If modules instantiated their own dependencies, unit testing would require mocking at a much deeper level.

---

## Known Limitation — KL-001: `send_warning()` uses a static countdown

**Current behaviour (Day 3):** `main.py` calls `notifier.send_warning(config.locking.warning_seconds_before_lock)` — a fixed value from config (default: 10 seconds). The notification always says "10 seconds" regardless of how much time has actually elapsed since the FSM entered WARNING.

**Why this is acceptable now:** A single macOS notification fires once per WARNING entry (the FSM's `_warning_sent` flag ensures it). The notification is accurate at the moment it fires — it is sent exactly when `time_remaining <= warning_seconds_before_lock`, so the displayed value is correct at that instant.

**What to fix in Day 9:** The menubar app will display a live countdown by reading `warning_countdown` from `~/.doorman/status.json` (already written correctly by the FSM each tick). The notification itself can remain static. If a richer "updating notification" is ever wanted, `send_warning()` should accept the live elapsed time from the FSM tick and compute remaining seconds there rather than using the config value.