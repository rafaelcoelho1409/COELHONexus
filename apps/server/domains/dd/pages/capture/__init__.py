"""Capture — what a tier fetched, kept exactly as it came, under `ingestion/raw/{slug}/`. The one input every transform starts from."""
from __future__ import annotations
from . import domain, entities, params, service


__all__ = ["domain", "entities", "params", "service"]
