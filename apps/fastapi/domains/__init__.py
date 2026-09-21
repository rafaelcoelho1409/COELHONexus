"""domains — business domains (bounded contexts): dd (Docs Distiller),
rr (Research Radar), ycs (YouCanSee), settings (external endpoints +
credential store).

Each domain owns its router, schemas, service/graph code, tasks and exceptions.
Cross-domain references go through the dotted `domains.*` path (see
docs/CODE-CONVENTIONS.md §8) rather than bare `from domains.x.y import z` imports.
"""
from __future__ import annotations

from . import dd, rr, settings, ycs
