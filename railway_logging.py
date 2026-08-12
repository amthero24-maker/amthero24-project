"""Railway-aware logging stream routing for truthful severity classification.

Railway derives log severity from the process stream. Informational records therefore go
to stdout, while warnings and errors remain on stderr. The existing global LogRecord
factory in ``log_safety`` continues to redact every record before formatting.
"""
from __future__ import annotations

import logging
from typing import Any

_LEVELS = {
    "NOTSET": logging.NOTSET,
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
}


class MaxLevelFilter(logging.Filter):
    """Allow records at or below one configured level."""

    def __init__(self, max_level: int | str = logging.INFO) -> None:
        super().__init__()
        self.max_level = _coerce_level(max_level)

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.max_level


def _coerce_level(value: Any) -> int:
    if isinstance(value, bool):
        return logging.INFO
    if isinstance(value, int):
        return max(logging.NOTSET, min(value, logging.CRITICAL))
    return _LEVELS.get(str(value or "").strip().upper(), logging.INFO)
