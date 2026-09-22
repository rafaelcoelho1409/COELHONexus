"""Ingestion observability — metric recorders. No shared span decorator
here (unlike planner/synth's `service.py`) — ingestion has exactly one
dispatcher call site (`runtime/dispatch/service.py:run`), so its span is
inline there rather than extracted; nothing else consumes it yet."""
from __future__ import annotations
from . import metrics

__all__ = ["metrics"]
