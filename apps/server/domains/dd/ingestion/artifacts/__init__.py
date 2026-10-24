"""Per-page artifact extraction — download media references at ingest time +
rewrite URLs to point at our MinIO copies. See service.py docstrings."""
from __future__ import annotations
from . import domain, entities, keys, params, service


__all__ = ["domain", "entities", "keys", "params", "service"]
