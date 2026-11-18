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
Q_PLANNER = domain.scoped_queue("planner", params.ENVIRONMENT)
Q_SYNTH = domain.scoped_queue("synth", params.ENVIRONMENT)
Q_YCS = domain.scoped_queue("ycs", params.ENVIRONMENT)
Q_BD = domain.scoped_queue("bd", params.ENVIRONMENT)


# qdrant_task / neo4j_task names dodge qdrant_client / neo4j package collisions
TASK_INCLUDE = [
    "domains.bd.ingestion.source.task",
    "domains.bd.ingestion.ai.task",
    "domains.bd.rag.ai.task",
    "domains.bd.distill.task",
    "domains.dd.pages.task",
    "domains.dd.pipeline.ai.task",
    "domains.ycs.ingestion.extract.task",
    "domains.ycs.ingestion.qdrant_task.task",
    "domains.ycs.ingestion.neo4j_task.task",
    "domains.ycs.ingestion.pipeline_task.task",
    "domains.ycs.ingestion.embedding_migration.task",
]


# per-module queue isolation: planner CPU / synth LLM / YCS embed each separated
TASK_ROUTES = {
    # The source module moved, while its explicitly registered task ID stays stable for queued deliveries.
    "domains.bd.ingestion.task.*": {"queue": Q_BD},
    "domains.bd.ingestion.ai.task.*": {"queue": Q_BD},
    "domains.bd.rag.ai.task.*": {"queue": Q_BD},
    # The independent RAG task retains its existing ID for queued deliveries and manifests.
    "domains.bd.ingestion.ai.retrieval_index.task.*": {"queue": Q_BD},
    "domains.bd.distill.task.*": {"queue": Q_BD},
    "domains.dd.pages.task.*": {"queue": Q_CRAWLER},
    # The moved task keeps its legacy broker id so runs queued before deploy still reach a registered handler.
    "domains.dd.ingestion.task.*": {"queue": Q_CRAWLER},
    # One task module serves both pipeline stages: synth-specific tasks precede the planner fallback.
    "domains.dd.pipeline.ai.task.run_single_chapter": {"queue": Q_SYNTH},
    "domains.dd.pipeline.ai.task.resume_synth": {"queue": Q_SYNTH},
    "domains.dd.pipeline.ai.task.run_study": {"queue": Q_SYNTH},
    "domains.dd.pipeline.ai.task.*": {"queue": Q_PLANNER},
    "domains.ycs.*": {"queue": Q_YCS},
}
