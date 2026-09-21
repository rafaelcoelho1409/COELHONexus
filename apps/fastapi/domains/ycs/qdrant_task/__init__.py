"""ycs/qdrant_task — Celery: ES transcripts → Qdrant hybrid + cache invalidation.

Named `qdrant_task/` (not `qdrant/`) to avoid colliding with the
`qdrant_client` Python package — `from domains.ycs.qdrant import ...`
would otherwise shadow `from qdrant_client import ...`.

`task.py` is excluded from this eager chain (§8 Exception 1 —
`import infra.celery.service` (use `infra.celery.service.app`) at module level); reach it via a direct
`from domains.ycs.qdrant_task.task import ingest_to_qdrant`."""
from __future__ import annotations
