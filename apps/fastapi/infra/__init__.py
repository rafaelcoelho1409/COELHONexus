"""infra — cross-cutting infrastructure (OTel/LangFuse telemetry, Celery).

No business logic. Domains and the API layer import from here; `infra`
never imports from `domains/`.

The subpackages below are re-exported so consumers write `import
domains, infra` and call through the full dotted path
(`infra.qdrant.service.get_qdrant()`, ...). `celery` is deliberately
NOT chained: its params read required env vars at module scope, so
importing it must stay an explicit per-caller choice
(`import infra.celery.service`, then `infra.celery.service.app`).
"""
from __future__ import annotations
from . import elasticsearch, langfuse, neo4j, otel, qdrant


__all__ = [
    "elasticsearch",
    "langfuse",
    "neo4j",
    "otel",
    "qdrant",
]
