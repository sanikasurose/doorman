"""Logging setup for Doorman. All other modules obtain their logger from here."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logger(
    debug_mode: bool,
    log_file: Path,
    max_bytes: int,
    backup_count: int,
) -> logging.Logger:
    """Configure and return the root doorman logger."""
    logger = logging.getLogger("doorman")
    logger.setLevel(logging.DEBUG if debug_mode else logging.INFO)
    logger.handlers.clear()

    if debug_mode:
        handler: logging.Handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
    else:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
        )
        handler.setLevel(logging.INFO)

    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)
    return logger
