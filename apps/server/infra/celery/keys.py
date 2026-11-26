"""celery keys — broker routing namespace: queue names + task paths + routes.

These are Celery routing KEYS (`task_default_queue`, `task_routes`
keys, `app.conf.include` entries) — the strings the broker, the
workers (`-Q` flags via `Values.celery.queues`), and the routers must
agree on. Queue names stay env-scoped via `domain.scoped_queue` (see
its docstring for the Helm-mirror contract).
"""

from __future__ import annotations

from . import domain, params


Q_DEFAULT = domain.scoped_queue("default", params.ENVIRONMENT)
Q_CRAWLER = domain.scoped_queue("crawler", params.ENVIRONMENT)
Q_YCS = domain.scoped_queue("ycs", params.ENVIRONMENT)
Q_BD = domain.scoped_queue("bd", params.ENVIRONMENT)


# qdrant_task / neo4j_task names dodge qdrant_client / neo4j package collisions
TASK_INCLUDE = [
    "domains.bd.ingestion.source.task",
    "domains.bd.ingestion.ai.task",
    "domains.bd.rag.ai.task",
    "domains.bd.rag.embedding_migration.task",
    "domains.bd.distill.ai.task",
    "domains.ycs.ingestion.extract.task",
    "domains.ycs.ingestion.qdrant_task.task",
    "domains.ycs.ingestion.neo4j_task.task",
    "domains.ycs.ingestion.pipeline_task.task",
    "domains.ycs.ingestion.embedding_migration.task",
]


# per-module queue isolation: BD and YCS work each have an independent queue
TASK_ROUTES = {
    # The source module moved, while its explicitly registered task ID stays stable for queued deliveries.
    "domains.bd.ingestion.task.*": {"queue": Q_BD},
    "domains.bd.ingestion.ai.task.*": {"queue": Q_BD},
    "domains.bd.rag.ai.task.*": {"queue": Q_BD},
    "domains.bd.rag.embedding_migration.*": {"queue": Q_BD},
    # The independent RAG task retains its existing ID for queued deliveries and manifests.
    "domains.bd.ingestion.ai.retrieval_index.task.*": {"queue": Q_BD},
    "domains.bd.distill.task.*": {"queue": Q_BD},
    "domains.ycs.*": {"queue": Q_YCS},
}
