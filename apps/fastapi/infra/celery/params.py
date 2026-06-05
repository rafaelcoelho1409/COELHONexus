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


# Task modules loaded at worker startup.
TASK_INCLUDE = [
    "domains.dd.ingestion.task",
    "domains.dd.planner.task",
    "domains.dd.synth.task",
]


# Routing — queue per task module. Isolation prevents the planner's
# CPU-heavy clustering from contending with HTTP-fetch ingestion, and
# the synth's LLM+CoCoA bursts from contending with planner.
TASK_ROUTES = {
    "domains.dd.ingestion.task.*": {"queue": Q_CRAWLER},
    "domains.dd.planner.task.*":   {"queue": Q_PLANNER},
    "domains.dd.synth.task.*":     {"queue": Q_SYNTH},
}
