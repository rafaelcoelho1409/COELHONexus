"""ycs/embedding_migration — consent-gated re-embed when the configured
embedding model changes. See `service.py` for the full design.

`task.py` is excluded from this eager chain (docs/CODE-CONVENTIONS.md §8
Exception 1 — `import infra.celery.service` (use `infra.celery.service.app`) at module level requires
Celery/Redis env vars); reach it via a direct
`from domains.ycs.embedding_migration.task import finalize_embedding_migration`."""
from __future__ import annotations
from . import domain, keys, params, service
