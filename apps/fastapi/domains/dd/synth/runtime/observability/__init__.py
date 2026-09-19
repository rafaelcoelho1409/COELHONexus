"""Synth observability — OTel span helpers (service.py) + pipeline metrics (metrics.py); instruments in infra.otel.metrics_registry."""
from __future__ import annotations
from . import metrics, service



__all__ = ["metrics", "service"]
