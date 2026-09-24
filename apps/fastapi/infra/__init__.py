"""infra — cross-cutting infrastructure (OTel/LangFuse telemetry, Celery).

No business logic. Domains and the API layer import from here; `infra`
never imports from `domains/`.

The subpackages below are re-exported LAZILY (PEP 562 module
`__getattr__`, 2026-09-24) so consumers can still write `import
domains, infra` and call through the full dotted path
(`infra.qdrant.service.get_qdrant()`, ...) without `import infra`
alone paying for all five subpackages every time. Before this, a
caller that only ever touches `infra.qdrant` still imported
elasticsearch/langfuse/neo4j/otel too — verified live at ~27s for
`import infra` cold, ~10-16s for ANY single `infra.X`, because
Python must fully run this file (which eagerly imported all five)
before it can reach `infra.X` at all. Each name below now only
imports on first actual access, and every subsequent access is a
plain attribute lookup (Python caches the result on this module via
`globals()[name] = module`) — zero behavior change for callers,
just no more forced full-tree import.

`celery` is deliberately NOT re-exported here at all (lazily or
otherwise): its params read required env vars at module scope, so
importing it must stay an explicit per-caller choice
(`import infra.celery.service`, then `infra.celery.service.app`).
"""
from __future__ import annotations
from typing import TYPE_CHECKING
import importlib


if TYPE_CHECKING:
    from . import elasticsearch, langfuse, neo4j, otel, qdrant


__all__ = [
    "elasticsearch",
    "langfuse",
    "neo4j",
    "otel",
    "qdrant",
]


def __getattr__(name: str):
    if name in __all__:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
