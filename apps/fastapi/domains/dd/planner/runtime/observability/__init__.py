"""Planner observability — OTel spans + planner metrics."""
from __future__ import annotations

from .metrics import record_planner_run
from .service import attach_span_attrs, traced


__all__ = ["attach_span_attrs", "traced", "record_planner_run"]
