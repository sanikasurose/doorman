"""Config loading and validation from ~/.doorman/config.toml. Raises ConfigError on any problem."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """Raised when config is missing, malformed, or has wrong types."""


# ---------------------------------------------------------------------------
# Section dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DetectionConfig:
    fps: int
    confidence_threshold: float
    window_size: int
    required_absent_frames: int
    dynamic_fps_scaling: bool


@dataclass(frozen=True)
class LockingConfig:
    absence_timeout_seconds: int
    warning_seconds_before_lock: int


@dataclass(frozen=True)
class SessionConfig:
    input_grace_seconds: int
    check_video_calls: bool
    check_screensaver: bool


@dataclass(frozen=True)
class RecognitionConfig:
    enabled: bool
    tolerance: float


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    log_file: str
    max_log_size_mb: int
    backup_count: int


@dataclass(frozen=True)
class Config:
    detection: DetectionConfig
    locking: LockingConfig
    session: SessionConfig
    recognition: RecognitionConfig
    logging: LoggingConfig


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _section(data: dict, name: str) -> dict:
    if name not in data:
        raise ConfigError(f"Missing required config section '[{name}]'")
    if not isinstance(data[name], dict):
        raise ConfigError(f"Config section '[{name}]' must be a table")
    return data[name]


def _require(section_name: str, data: dict, key: str, expected: type | tuple) -> object:
    if key not in data:
        raise ConfigError(f"[{section_name}] missing required key '{key}'")
    val = data[key]
    if not isinstance(val, expected):
        type_names = (
            " or ".join(t.__name__ for t in expected)
            if isinstance(expected, tuple)
            else expected.__name__
        )
        raise ConfigError(
            f"[{section_name}] '{key}' must be {type_names}, got {type(val).__name__}"
        )
    return val


def _int(section: str, data: dict, key: str) -> int:
    val = _require(section, data, key, int)
    if isinstance(val, bool):
        raise ConfigError(f"[{section}] '{key}' must be int, got bool")
    return int(val)


def _float(section: str, data: dict, key: str) -> float:
    # Accept int literals in TOML (e.g. tolerance = 1) and promote to float
    return float(_require(section, data, key, (int, float)))


def _bool(section: str, data: dict, key: str) -> bool:
    return bool(_require(section, data, key, bool))


def _str(section: str, data: dict, key: str) -> str:
    return str(_require(section, data, key, str))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config(path: Path) -> Config:
    """Load and validate ~/.doorman/config.toml. Raises ConfigError on any problem."""
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Config file is not valid TOML: {exc}") from exc

    det = _section(data, "detection")
    lock = _section(data, "locking")
    sess = _section(data, "session")
    rec = _section(data, "recognition")
    log = _section(data, "logging")

    return Config(
        detection=DetectionConfig(
            fps=_int("detection", det, "fps"),
            confidence_threshold=_float("detection", det, "confidence_threshold"),
            window_size=_int("detection", det, "window_size"),
            required_absent_frames=_int("detection", det, "required_absent_frames"),
            dynamic_fps_scaling=_bool("detection", det, "dynamic_fps_scaling"),
        ),
        locking=LockingConfig(
            absence_timeout_seconds=_int("locking", lock, "absence_timeout_seconds"),
            warning_seconds_before_lock=_int("locking", lock, "warning_seconds_before_lock"),
        ),
        session=SessionConfig(
            input_grace_seconds=_int("session", sess, "input_grace_seconds"),
            check_video_calls=_bool("session", sess, "check_video_calls"),
            check_screensaver=_bool("session", sess, "check_screensaver"),
        ),
        recognition=RecognitionConfig(
            enabled=_bool("recognition", rec, "enabled"),
            tolerance=_float("recognition", rec, "tolerance"),
        ),
        logging=LoggingConfig(
            level=_str("logging", log, "level"),
            log_file=_str("logging", log, "log_file"),
            max_log_size_mb=_int("logging", log, "max_log_size_mb"),
            backup_count=_int("logging", log, "backup_count"),
        ),
    )
