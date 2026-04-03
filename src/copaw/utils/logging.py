# -*- coding: utf-8 -*-
"""Logging setup for CoPaw: console output and optional file handler."""

import logging
import logging.handlers
import os
import platform
import sys
from functools import lru_cache
from pathlib import Path

# Rotating file handler limits (idempotent add avoids duplicate handlers)
_COPAW_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB
_COPAW_LOG_BACKUP_COUNT = 3


_LEVEL_MAP = {
    "critical": logging.CRITICAL,
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}

# Top-level name for this package; only loggers under this name are shown.
LOG_NAMESPACE = "copaw"


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[34m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[41m\033[97m",
    }
    RESET = "\033[0m"

    def format(self, record):
        # Disable colors if output is not a terminal (e.g. piped/redirected)
        use_color = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
        color = self.COLORS.get(record.levelno, "") if use_color else ""
        reset = self.RESET if use_color else ""
        level = f"{color}{record.levelname}{reset}"

        full_path = record.pathname
        cwd = os.getcwd()
        # Use os.path for cross-platform path prefix stripping
        try:
            if os.path.commonpath([full_path, cwd]) == cwd:
                full_path = os.path.relpath(full_path, cwd)
        except ValueError:
            # Different drives on Windows (e.g., C: vs D:) are not comparable.
            pass

        prefix = f"{level} {full_path}:{record.lineno}"
        original_msg = super().format(record)

        return f"{prefix} | {original_msg}"


class SuppressPathAccessLogFilter(logging.Filter):
    """
    Filter out uvicorn access log lines whose message contains any of the
    given path substrings. path_substrings: list of substrings; if any
    appears in the log message, the record is suppressed.
    Empty list = allow all.
    """

    def __init__(self, path_substrings: list[str]) -> None:
        super().__init__()
        self.path_substrings = path_substrings

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.path_substrings:
            return True
        try:
            msg = record.getMessage()
            return not any(s in msg for s in self.path_substrings)
        except Exception:
            return True


@lru_cache(64)
def has_tg_token(content: str) -> bool:
    import re

    return bool(re.compile(r".*?([0-9]{8,10}):([a-zA-Z0-9_-]{35}).*?").match(content))


def log_filter(record: logging.LogRecord) -> bool:
    if isinstance(record.args, tuple):
        args = list(record.args)
    else:
        # noinspection PyTypeChecker
        args = list(dict(record.args).values())
    for i in args + [record.msg]:
        if has_tg_token(str(i)):
            return False

    return True


# noinspection PyPackageRequirements
def setup_logger(level: int | str = logging.INFO):
    """Configure logging to only output from this package (copaw), not deps."""
    import loguru
    from logre.handler import default_handler

    default_handler.addFilter(log_filter)

    loguru.logger.remove()
    loguru.logger.add(
        default_handler, format="%(message)s", colorize=True, level="INFO"
    )

    if isinstance(level, str):
        level = _LEVEL_MAP.get(level.lower(), logging.INFO)

    for name in [None, "uvicorn"]:
        logging.getLogger(name).handlers = [default_handler]

    from logre import logger

    # Only attach handler to our namespace so only copaw.* logs are printed.
    logger.setLevel(level)
    logger.propagate = False

    return logger


def add_copaw_file_handler(log_path: Path) -> None:
    """Add a file handler to the copaw logger for daemon logs.

    Windows/Linux: Uses simple FileHandler to avoid file locking issues.
    macOS: Uses RotatingFileHandler with automatic log rotation.

    Idempotent: if the logger already has a file handler for the same path,
    no new handler is added (avoids duplicate lines and leaked descriptors
    when lifespan runs multiple times in the same process).

    Args:
        log_path: Path to the log file (e.g. WORKING_DIR / "copaw.log").
    """
    log_path = Path(log_path).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOG_NAMESPACE)
    for handler in logger.handlers:
        base: str | None = getattr(handler, "baseFilename", None)
        if base is not None and Path(base).resolve() == log_path:
            return

    is_windows_or_linux = platform.system() in ("Windows", "Linux")
    if is_windows_or_linux:
        file_handler = logging.FileHandler(
            log_path,
            encoding="utf-8",
            mode="a",
        )
    else:
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            encoding="utf-8",
            maxBytes=_COPAW_LOG_MAX_BYTES,
            backupCount=_COPAW_LOG_BACKUP_COUNT,
        )

    if platform.system() == "Windows":
        file_handler.setLevel(logging.INFO)

    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S"),
    )
    logger.addHandler(file_handler)
