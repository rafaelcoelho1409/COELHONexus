"""Celery app instance + worker_process_init signal handler.

Per-worker init provisions MinIO bucket + warms the BYOK credential
store + builds the dynamic LLM catalog so the first task doesn't pay a
cold-start cost. All three steps are best-effort — failures degrade
gracefully (env-key fallback / static catalog / first-task bucket 404)
without blocking worker boot.
"""
from __future__ import annotations

import asyncio
import logging
import sys

# Celery prefork children don't inherit sys.path modifications from the
# parent, so ensure /app is on path before any task imports.
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from celery import Celery
from celery.signals import worker_process_init

from .params import (
    REDIS_URL,
    Q_DEFAULT,
    TASK_INCLUDE,
    TASK_ROUTES,
)


logger = logging.getLogger(__name__)


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
    "task_routes": TASK_ROUTES,
    "worker_prefetch_multiplier": 1,
    "broker_connection_retry_on_startup": True,
    # Flower events — required or Flower's task list stays empty.
    "worker_send_task_events": True,
    "task_send_sent_event": True,
    "event_queue_expires": 60.0,
    "timezone": "UTC",
})

app.conf.include = TASK_INCLUDE


@worker_process_init.connect
def _worker_process_init(**_kwargs) -> None:
    # OTel SDK — TracerProvider + dual-export (Alloy gRPC → Tempo, LangFuse
    # HTTP → LLM observations) + Celery auto-instrumentation. MUST run before
    # any other init step so subsequent setup spans (catalog build, etc.)
    # are captured, and so every fork has its own provider (the parent's SDK
    # state doesn't survive fork()).
    try:
        from infra.otel import init_otel_for_celery_worker
        init_otel_for_celery_worker()
    except Exception as e:
        logger.warning(
            f"[worker-init] OTel init failed "
            f"({type(e).__name__}: {e}); spans will be no-ops, no LangFuse/"
            f"Tempo data from this worker"
        )
    # MinIO bucket — so the first ingest task can put_object without 404.
    try:
        from domains.dd.ingestion.storage import get_storage
        asyncio.run(get_storage().ensure_bucket())
    except Exception as e:
        logger.warning(
            f"[worker-init] MinIO ensure_bucket failed "
            f"({type(e).__name__}: {e}); ingestion tasks will fail until "
            f"MinIO is reachable + creds are correct"
        )
    # BYOK credential store warm — synth/planner tasks resolve user-supplied
    # keys from cache instead of a cold MinIO GET. Env-key fallback otherwise.
    try:
        from domains.llm.credentials import warm as warm_credentials
        warm_credentials()
    except Exception as e:
        logger.warning(
            f"[worker-init] LLM credential store warm failed "
            f"({type(e).__name__}: {e}); rotator will use env keys only"
        )
    # Selection-driven dynamic catalog — first synth/judge task resolves
    # against the user's selected models. Rebuilds lazily on /settings change
    # (Redis settings-gen check on the bandit path).
    try:
        from domains.llm.rotator.chain import init_dynamic_catalog_sync
        init_dynamic_catalog_sync()
    except Exception as e:
        logger.warning(
            f"[worker-init] dynamic catalog init failed "
            f"({type(e).__name__}: {e}); rotator will use the static catalog"
        )
