"""domains — business domains (bounded contexts): dd (Docs Distiller),
rr (Research Radar), ycs (YouTube Content Search), settings (external endpoints +
credential store).

Each domain owns its router, schemas, service/graph code, tasks and exceptions.
Cross-domain references go through the dotted `domains.*` path (see
docs/CODE-CONVENTIONS.md §8) rather than bare `from domains.x.y import z` imports.

The four domains below are re-exported LAZILY (PEP 562 module
`__getattr__`, 2026-09-24) — see `infra/__init__.py`'s docstring for
the full rationale/measurements. Same fix, same reason: a caller that
only ever touches `domains.ycs` used to pay for dd + rr + settings too,
since reaching `domains.ycs` at all required this file to first run
to completion. Each domain now only imports on first actual access.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
import importlib


if TYPE_CHECKING:
    from . import dd, rr, settings, ycs


__all__ = ["dd", "rr", "settings", "ycs"]


def __getattr__(name: str):
    if name in __all__:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
