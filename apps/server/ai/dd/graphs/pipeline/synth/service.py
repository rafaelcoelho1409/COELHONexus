"""Helpers every node of the chapter graph shares."""
from __future__ import annotations
from .... import graphs

from typing import Any


def failure(field: str, stats_field: str, reason: str, error: str, started: float, **extra: Any) -> dict:
    """The patch a node returns when it cannot run: an empty output, the reason in its stats, and the run marked failed."""
    return {
        field: "",
        stats_field: {"skipped": reason, "wall_ms": graphs.service.elapsed_ms(started), **extra},
        "status": "failed",
        "error": error,
    }
