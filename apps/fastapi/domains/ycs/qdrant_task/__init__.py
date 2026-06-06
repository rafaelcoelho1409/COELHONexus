"""ycs/qdrant_task — Celery: ES transcripts → Qdrant hybrid + cache invalidation.

Named `qdrant_task/` (not `qdrant/`) to avoid colliding with the
`qdrant_client` Python package — `from domains.ycs.qdrant import ...`
would otherwise shadow `from qdrant_client import ...`."""
from .task import ingest_to_qdrant, invalidate_cache


__all__ = ["ingest_to_qdrant", "invalidate_cache"]
