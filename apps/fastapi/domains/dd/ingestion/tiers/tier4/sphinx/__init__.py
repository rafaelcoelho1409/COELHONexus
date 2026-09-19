"""Sphinx discovery: domain.py parses objects.inv + extracts sidebar/body
links + splits multi-topic pages; service.py fetches objects.inv and does
DOM toctree BFS discovery."""
from __future__ import annotations

from . import domain, entities, params, patterns, service


__all__ = ["domain", "entities", "params", "patterns", "service"]
