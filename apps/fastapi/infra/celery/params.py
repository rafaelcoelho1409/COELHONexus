"""Celery tunables — Redis URL, queues, task discovery + routing."""
from __future__ import annotations

import os


_REDIS_HOST = os.environ["REDIS_HOST"]
_REDIS_PORT = os.environ["REDIS_PORT"]
_REDIS_PASSWORD = os.environ["REDIS_PASSWORD"]

if _REDIS_PASSWORD:
    REDIS_URL = f"redis://:{_REDIS_PASSWORD}@{_REDIS_HOST}:{_REDIS_PORT}"
else:
    REDIS_URL = f"redis://{_REDIS_HOST}:{_REDIS_PORT}"


# ENVIRONMENT-suffixed queues so dev/prod don't steal each other's tasks
# on the shared Redis broker.
ENVIRONMENT = os.environ["ENVIRONMENT"].lower()
Q_DEFAULT = f"default-{ENVIRONMENT}"
Q_CRAWLER = f"crawler-{ENVIRONMENT}"
Q_PLANNER = f"planner-{ENVIRONMENT}"
Q_SYNTH = f"synth-{ENVIRONMENT}"
Q_YCS = f"ycs-{ENVIRONMENT}"
# RR's long DeepAgents runs on own queue to avoid contending with DD/YCS
Q_RR = f"rr-{ENVIRONMENT}"


# qdrant_task / neo4j_task names dodge qdrant_client / neo4j package collisions
TASK_INCLUDE = [
    "domains.dd.ingestion.task",
    "domains.dd.planner.task",
    "domains.dd.synth.task",
    "domains.ycs.extract.task",
    "domains.ycs.qdrant_task.task",
    "domains.ycs.neo4j_task.task",
    "domains.ycs.pipeline_task.task",
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
