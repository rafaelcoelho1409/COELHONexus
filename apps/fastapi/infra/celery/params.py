"""Celery tunables — Redis URL, queues, task discovery + routing."""
from __future__ import annotations

import os


# Strict env reads — Helm provides all four (REDIS_HOST/PORT in
# _helpers.tpl, REDIS_PASSWORD via envValueFrom, ENVIRONMENT from
# .Values.environment). Empty REDIS_PASSWORD = no-auth case, handled
# below.
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
# Research Radar (3rd feature) — own queue so its long agent runs don't
# contend with DD planner/synth or YCS crawler/embed bursts.
Q_RR = f"rr-{ENVIRONMENT}"


# Task modules loaded at worker startup. YCS task modules are the Wave 4
# port of deprecated `tasks/youtube/{crawler,qdrant,neo4j,pipeline}.py`
# (renamed: `extract` for crawler; `qdrant_task` / `neo4j_task` to dodge
# the `qdrant_client` / `neo4j` package collisions; `pipeline_task` for
# the chain wrapper).
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


# Routing — queue per task module. Isolation prevents the planner's
# CPU-heavy clustering from contending with HTTP-fetch ingestion, and
# the synth's LLM+CoCoA bursts from contending with planner. YCS gets its
# own queue so transcript/embed bursts don't contend with DD. RR's
# DeepAgents runs are LLM-bound + long — kept on its own queue so a
# slow agent doesn't block DD/YCS short-running tasks.
TASK_ROUTES = {
    "domains.dd.ingestion.task.*": {"queue": Q_CRAWLER},
    "domains.dd.planner.task.*":   {"queue": Q_PLANNER},
    "domains.dd.synth.task.*":     {"queue": Q_SYNTH},
    "domains.ycs.*":               {"queue": Q_YCS},
    "domains.rr.task.*":           {"queue": Q_RR},
}
