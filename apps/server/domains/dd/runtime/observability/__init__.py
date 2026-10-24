"""DD shared observability — `db.*` Postgres spans for the checkpoints-
table reads that both Planner and Synth routers issue directly."""
from __future__ import annotations
from . import spans

__all__ = ["spans"]
