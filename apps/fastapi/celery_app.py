"""
Celery application — base shell.

Broker + result backend point at the in-cluster Redis. No task modules are
registered yet; the worker boots cleanly and idles on the default queue
until tasks are added via `app.conf.include`.
"""
import os
import sys

# Celery prefork children don't inherit sys.path modifications from the
# parent, so ensure /app is on path before any task imports.
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from celery import Celery


REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")

if REDIS_PASSWORD:
    REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}"
else:
    REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}"

# Environment-scoped queue names so dev and prod don't steal each other's
# tasks when they share the same Redis broker. ENVIRONMENT comes from Helm
# (values.yaml): "local" or "production".
ENVIRONMENT = os.environ.get("ENVIRONMENT", "local").lower()
Q_DEFAULT = f"default-{ENVIRONMENT}"
Q_CRAWLER = f"crawler-{ENVIRONMENT}"
# Planner queue — isolated from crawler so CPU-heavy UMAP+HDBSCAN+GMM
# work doesn't contend with HTTP-fetch tasks. See domains/dd/planner/task.py.
Q_PLANNER = f"planner-{ENVIRONMENT}"


app = Celery("coelhonexus")

app.config_from_object({
    "broker_url": REDIS_URL,
    "result_backend": REDIS_URL,
    "task_serializer": "json",
    "result_serializer": "json",
    "accept_content": ["json"],
    "result_expires": 86400,
    "task_track_started": True,
    "task_default_queue": Q_DEFAULT,
    "task_routes": {
        # Docs Distiller ingestion is HTTP-fetch heavy → crawler queue
        "domains.dd.ingestion.task.*": {"queue": Q_CRAWLER},
        # Planner runs the 8-node LangGraph (CPU-heavy cluster/refine +
        # LLM-bound off_topic/label/reduce) → planner queue
        "domains.dd.planner.task.*": {"queue": Q_PLANNER},
    },
    "worker_prefetch_multiplier": 1,
    "broker_connection_retry_on_startup": True,
    # Flower events — required or Flower's task list stays empty.
    "worker_send_task_events": True,
    "task_send_sent_event": True,
    "event_queue_expires": 60.0,
    "timezone": "UTC",
})

# Task module discovery. Add one entry per `tasks/<feature>/<module>.py`.
app.conf.include = [
    "domains.dd.ingestion.task",
    "domains.dd.planner.task",
]


# =============================================================================
# Per-worker init — provision MinIO bucket so the first ingest task can
# put_object without a 404 on the bucket. Idempotent.
# =============================================================================
from celery.signals import worker_process_init


@worker_process_init.connect
def _ensure_minio_bucket(**_kwargs) -> None:
    import asyncio
    import logging
    logger = logging.getLogger(__name__)
    try:
        from domains.dd.ingestion.storage import get_storage
        asyncio.run(get_storage().ensure_bucket())
    except Exception as e:
        logger.warning(
            f"[worker-init] MinIO ensure_bucket failed "
            f"({type(e).__name__}: {e}); ingestion tasks will fail until "
            f"MinIO is reachable + creds are correct"
        )
