"""
Celery Application Configuration

CONCEPT: Celery is a distributed task queue. This file configures:
- Broker: Redis (same instance used for caching and LangGraph checkpoints)
- Result backend: Redis (stores task status and results)
- Queues: crawler, embedding, llm (route tasks to specialized workers)
- Serialization: JSON (results must be JSON-serializable)

Workers run the same Docker image as FastAPI with a different command:
  FastAPI:  uvicorn app:app --host 0.0.0.0 --port 8000
  Worker:   celery -A celery_app worker -Q crawler,embedding,llm -c 3

The worker imports and runs the same code as FastAPI
(services/ingestion.py, services/graph_builder.py, helpers.py)
but executes it in Celery's process, not inside uvicorn.
"""
import os
import sys

# Ensure /app is in Python path BEFORE any imports.
# Celery worker process may not inherit the working directory in sys.path.
# This must run here (celery_app.py loads first) so all task imports work.
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from celery import Celery

# Redis URL — same pattern as app.py
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")

if REDIS_PASSWORD:
    REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}"
else:
    REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}"

# Environment-scoped queue names.
# Both skaffold-dev and argocd-prod point at the SAME Redis instance (shared
# broker keeps Flower/monitoring unified). Without a suffix, both environments'
# workers compete on the same queue names (`llm`, `crawler`, ...) and either
# can steal the other's tasks.
# Suffix makes the intent explicit: tasks published by the dev FastAPI land on
# `llm-local`; prod FastAPI's tasks land on `llm-production`. Workers listen
# only to their own environment's queues (see Helm celery deployment).
# ENVIRONMENT is set by Helm (values.yaml): "local" or "production".
ENVIRONMENT = os.environ.get("ENVIRONMENT", "local").lower()
Q_CRAWLER = f"crawler-{ENVIRONMENT}"
Q_EMBEDDING = f"embedding-{ENVIRONMENT}"
Q_LLM = f"llm-{ENVIRONMENT}"
Q_DEFAULT = f"default-{ENVIRONMENT}"

# Celery app
app = Celery("coelhonexus")

app.config_from_object({
    # Broker + result backend: both Redis
    "broker_url": REDIS_URL,
    "result_backend": REDIS_URL,
    # Serialization: JSON (all task args and results must be JSON-serializable)
    "task_serializer": "json",
    "result_serializer": "json",
    "accept_content": ["json"],
    # Result expiry: 24 hours (auto-cleanup old results from Redis)
    "result_expires": 86400,
    # Track task start time (enables STARTED state in Flower)
    "task_track_started": True,
    # Task routing: direct tasks to specialized queues (env-suffixed — see above)
    "task_routes": {
        "tasks.youtube.crawler.*": {"queue": Q_CRAWLER},
        "tasks.youtube.qdrant.*": {"queue": Q_EMBEDDING},
        "tasks.youtube.neo4j.*": {"queue": Q_LLM},
        "tasks.youtube.pipeline.*": {"queue": Q_CRAWLER},
        # Knowledge Distiller — LLM-heavy pipeline, same queue as graph extraction
        "tasks.knowledge.distiller.*": {"queue": Q_LLM},
        # KD exports (Pandoc/xelatex/genanki) — CPU-bound but short; share the llm queue.
        "tasks.knowledge.export.*": {"queue": Q_LLM},
    },
    # Default queue for unrouted tasks
    "task_default_queue": Q_DEFAULT,
    # Worker: prefetch 1 task at a time (long-running tasks shouldn't queue up)
    "worker_prefetch_multiplier": 1,
    # Acknowledge task AFTER execution (not before) — prevents task loss on crash
    "task_acks_late": True,
    # Reject and requeue tasks when worker is killed (OOM, SIGKILL)
    "task_reject_on_worker_lost": True,
    # Timezone
    "timezone": "UTC",
})

# Explicitly include task modules (autodiscover has import issues with nested packages)
app.conf.include = [
    "tasks.youtube.crawler",
    "tasks.youtube.qdrant",
    "tasks.youtube.neo4j",
    "tasks.youtube.pipeline",
    "tasks.knowledge.distiller",
    "tasks.knowledge.export",
]
