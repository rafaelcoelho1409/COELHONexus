"""OTLP export-failure log-spam dampener.

First-then-suppress per (logger, level, msg-prefix) preserves the initial
degradation signal without flooding app logs when the collector is
unreachable. Ported 1:1 from apps/fastapi/infra/otel/filters.py.
"""
from __future__ import annotations

import logging
import time

from .params import DEDUPE_LOG_INTERVAL_S, OTEL_NOISY_LOGGERS


class _DedupeRateLimitFilter(logging.Filter):
    """Keys on msg-prefix so variants only differing in trailing
    'retrying in N.NNs' still collapse to one log per window."""

    def __init__(self, interval_s: float = DEDUPE_LOG_INTERVAL_S):
        super().__init__()
        self._interval = interval_s
        self._last: dict = {}

    def filter(self, record: logging.LogRecord) -> bool:
        key = (record.name, record.levelno, str(record.msg)[:80])
        now = time.monotonic()
        prev = self._last.get(key)
        if prev is None or (now - prev) >= self._interval:
            self._last[key] = now
            return True
        return False


_otel_log_filter = _DedupeRateLimitFilter()


def quiet_otel_export_logs() -> None:
    """Idempotent — same filter instance reused so re-init never double-adds."""
    for name in OTEL_NOISY_LOGGERS:
        lg = logging.getLogger(name)
        if _otel_log_filter not in lg.filters:
            lg.addFilter(_otel_log_filter)
