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
# RR's long DeepAgents runs on own queue to avoid contending with DD/YCS
Q_RR = domain.scoped_queue("rr", params.ENVIRONMENT)


# qdrant_task / neo4j_task names dodge qdrant_client / neo4j package collisions
TASK_INCLUDE = [
    "domains.dd.ingestion.task",
    "domains.dd.planner.task",
    "domains.dd.synth.task",
    "domains.ycs.extract.task",
    "domains.ycs.qdrant_task.task",
    "domains.ycs.neo4j_task.task",
    "domains.ycs.pipeline_task.task",
    "domains.ycs.embedding_migration.task",
    # Research Radar (3rd feature)
    "domains.rr.task",
]


# per-module queue isolation: planner CPU / synth LLM / YCS embed / RR long-running each separated
TASK_ROUTES = {
    "domains.dd.ingestion.task.*": {"queue": Q_CRAWLER},
    "domains.dd.planner.task.*":   {"queue": Q_PLANNER},
    "domains.dd.synth.task.*":     {"queue": Q_SYNTH},
    "domains.ycs.*":               {"queue": Q_YCS},
    "domains.rr.task.*":           {"queue": Q_RR},
}
