"""Celery app instance + worker_process_init signal handler.

The only module that imports the `celery` library: everything the rest of
the code needs from it is reached dotted through here — `app` (task
decorators), `chain` / `group` (canvas), `get_task_logger`, `AsyncResult`,
`get_result(task_id)` and `revoke(task_id, ...)`.

Per-worker init provisions the MinIO bucket + warms the endpoint
credential store. Both steps are best-effort — failures degrade
gracefully (env-key fallback / first-task bucket 404) without blocking
worker boot.

The log-record bootstrap below mirrors `main.py`'s on purpose instead
of importing it: the worker cannot import the FastAPI app (it would
pull the whole web lifespan into every fork).
"""
from __future__ import annotations
import domains, infra

import asyncio
import logging
import sys

# Celery prefork children don't inherit sys.path modifications from the
# parent, so ensure /app is on path before any task imports.
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "[trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] %(message)s"
)


def _install_log_record_defaults() -> None:
    old_factory = logging.getLogRecordFactory()
    if getattr(old_factory, "_coelho_otel_defaults", False):
        return

    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.otelTraceID = getattr(record, "otelTraceID", "0")
        record.otelSpanID = getattr(record, "otelSpanID", "0")
        return record

    record_factory._coelho_otel_defaults = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(record_factory)


_install_log_record_defaults()
logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)


from . import keys, params

from celery import Celery, chain, group
from celery.result import AsyncResult
from celery.signals import worker_process_init
from celery.utils.log import get_task_logger


logger = logging.getLogger(__name__)


app = Celery("coelhonexus")

app.config_from_object({
    "broker_url": params.REDIS_URL,
    "result_backend": params.REDIS_URL,
    "task_serializer": "json",
    "result_serializer": "json",
    "accept_content": ["json"],
    "result_expires": params.RESULT_EXPIRES_S,
    "task_track_started": True,
    "task_default_queue": keys.Q_DEFAULT,
    "task_routes": keys.TASK_ROUTES,
    "worker_prefetch_multiplier": params.WORKER_PREFETCH_MULTIPLIER,
    "broker_connection_retry_on_startup": True,
    # Flower events — required or Flower's task list stays empty.
    "worker_send_task_events": True,
    "task_send_sent_event": True,
    "event_queue_expires": params.EVENT_QUEUE_EXPIRES_S,
    "timezone": "UTC",
})

app.conf.include = keys.TASK_INCLUDE


def get_result(task_id: str) -> AsyncResult:
    """Result handle for a task id on this app's broker / result backend."""
    return AsyncResult(task_id, app=app)


def revoke(task_id: str, **kwargs) -> None:
    """Revoke a task; pass `terminate=True, signal="SIGTERM"` to kill a running one."""
    app.control.revoke(task_id, **kwargs)


@worker_process_init.connect(weak=False)
def _worker_process_init(**_kwargs) -> None:
    # OTel MUST run first: each fork needs its own provider (parent SDK state doesn't survive fork()).
    try:
        infra.opentelemetry.service.init_otel_for_celery_worker()
    except Exception as e:
        logger.warning(
            f"[worker-init] OTel init failed "
            f"({type(e).__name__}: {e}); spans will be no-ops, no LangFuse/"
            f"Tempo data from this worker"
        )
    try:
        asyncio.run(infra.minio.service.get_storage().ensure_bucket())
    except Exception as e:
        logger.warning(
            f"[worker-init] MinIO ensure_bucket failed "
            f"({type(e).__name__}: {e}); ingestion tasks will fail until "
            f"MinIO is reachable + creds are correct"
        )
    try:
        domains.settings.credentials.service.warm()
    except Exception as e:
        logger.warning(
            f"[worker-init] LLM credential store warm failed "
            f"({type(e).__name__}: {e}); endpoint clients will use env keys only"
        )


# Public surface for the rest of the code (the re-exports are intentional).
__all__ = [
    "AsyncResult", "app", "chain", "get_result", "get_task_logger", "group", "revoke",
]
