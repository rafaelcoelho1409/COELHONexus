"""Ingestion entry point: resolver → tier dispatch → post-process → finalize."""
from __future__ import annotations
from . import domain, params, service


__all__ = ["domain", "params", "service"]
