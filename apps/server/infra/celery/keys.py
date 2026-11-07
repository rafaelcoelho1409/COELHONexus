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
    "domains.bd.ingestion.task",
    "domains.bd.ingestion.ai.task",
    "domains.bd.distill.task",
    "domains.dd.ingestion.task",
    "domains.dd.ingestion.ai.task",
    "domains.dd.pipeline.ai.task",
    "domains.ycs.extract.task",
    "domains.ycs.qdrant_task.task",
    "domains.ycs.neo4j_task.task",
    "domains.ycs.pipeline_task.task",
    "domains.ycs.embedding_migration.task",
]


# per-module queue isolation: planner CPU / synth LLM / YCS embed each separated
TASK_ROUTES = {
    "domains.bd.ingestion.task.*": {"queue": Q_BD},
    "domains.bd.ingestion.ai.task.*": {"queue": Q_BD},
    "domains.bd.distill.task.*": {"queue": Q_BD},
    "domains.dd.ingestion.task.*": {"queue": Q_CRAWLER},
    "domains.dd.ingestion.ai.task.*": {"queue": Q_CRAWLER},
    # One task module serves both stages: the synth's three tasks are named first (the first matching route wins), the rest of the module is the planner's.
    "domains.dd.pipeline.ai.task.run_single_chapter": {"queue": Q_SYNTH},
    "domains.dd.pipeline.ai.task.resume_synth":       {"queue": Q_SYNTH},
    "domains.dd.pipeline.ai.task.run_study":          {"queue": Q_SYNTH},
    "domains.dd.pipeline.ai.task.*":                  {"queue": Q_PLANNER},
    "domains.ycs.*":               {"queue": Q_YCS},
}
