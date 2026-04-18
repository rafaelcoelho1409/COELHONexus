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
    # Task routing: direct tasks to specialized queues
    "task_routes": {
        "tasks.crawler.*": {"queue": "crawler"},
        "tasks.ingestion.*": {"queue": "embedding"},
        "tasks.graph.*": {"queue": "llm"},
        "tasks.pipeline.*": {"queue": "crawler"},
    },
    # Default queue for unrouted tasks
    "task_default_queue": "default",
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
    "tasks.crawler",
    "tasks.ingestion",
    "tasks.graph",
    "tasks.pipeline",
]
