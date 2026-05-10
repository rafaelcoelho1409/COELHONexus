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
        # KD ingestion-only (no LLM, but I/O-heavy — shares the llm queue's prefetch=1
        # behavior so a Tier-4 Playwright crawl doesn't starve a synth task waiting for an LLM slot)
        "tasks.knowledge.ingestion.*": {"queue": Q_LLM},
    },
    # Default queue for unrouted tasks
    "task_default_queue": Q_DEFAULT,
    # Worker: prefetch 1 task at a time (long-running tasks shouldn't queue up)
    "worker_prefetch_multiplier": 1,
    # Acknowledge task AFTER execution (not before) — prevents task loss on
    # crash. SAFE ONLY FOR IDEMPOTENT TASKS. Individual non-idempotent tasks
    # (Knowledge Distiller) override with `acks_late=False` at the @app.task
    # decorator — those tasks are user-triggered and resumable via
    # checkpointer/re-click, so auto-redelivery on worker crash creates
    # unwanted zombie runs rather than recovering real work.
    "task_acks_late": True,
    # Reject and requeue tasks when worker is killed (OOM, SIGKILL). Only
    # affects tasks that still have acks_late=True; no-op for KD distiller task.
    "task_reject_on_worker_lost": True,
    # Broker-level safety: cap how long an unacked message may live before
    # Redis makes it visible again. Default is 1h (3600). 7200 = 2h covers
    # YouTube tasks that may legitimately exceed 1h without triggering the
    # classic "running task duplicated mid-run" Redis bug. For KD tasks
    # (acks_late=False) this is a defense-in-depth setting that rarely
    # fires — messages are acked on pickup, never reaching the unacked pool.
    "broker_transport_options": {"visibility_timeout": 7200},
    # =========================================================================
    # Production hardening (added 2026-05-08, deep-research validated)
    # =========================================================================
    # Worker recycling — guards against memory leaks in long-running Python
    # workers (Playwright contexts, LLM client buffers, transformers caches).
    # Recycle worker after 50 tasks OR 2 GB RSS, whichever fires first.
    "worker_max_tasks_per_child": 50,
    "worker_max_memory_per_child": 2_000_000,  # 2 GB RSS in KB (Celery unit)
    # Task time limits — defensive cap for tasks that hang (Playwright frozen
    # context, LLM provider stuck mid-stream). Hard kill at 2h matches
    # broker_transport_options.visibility_timeout above, so an unacked
    # message can never re-fire while the original is still SIGKILLed
    # mid-flight. Soft limit gives 2 min for SoftTimeLimitExceeded cleanup.
    # Individual KD synth tasks override per-@app.task if they need more.
    "task_time_limit": 7200,
    "task_soft_time_limit": 7080,
    # Flower events — REQUIRED or Flower's task list stays empty. Equivalent
    # to passing -E on the worker CLI. Source: Celery Monitoring guide.
    "worker_send_task_events": True,
    "task_send_sent_event": True,
    "event_queue_expires": 60.0,
    # Celery 6 readiness — silences 5.x DeprecationWarning at boot and makes
    # broker-retry-on-startup behavior explicit (was implicit-true in 5.x).
    "broker_connection_retry_on_startup": True,
    # Redis broker hardening — in-cluster DNS can blip during k8s reschedules.
    # Keepalive + tighter timeout + retry-forever give faster, deterministic
    # recovery vs Celery's 120s default + finite-retry.
    "broker_pool_limit": 10,
    "redis_socket_keepalive": True,
    "redis_socket_timeout": 30,
    "redis_retry_on_timeout": True,
    "broker_connection_max_retries": None,
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
    "tasks.knowledge.ingestion",
]


# =============================================================================
# Worker startup — no pre-warm needed
# =============================================================================
# Embeddings go through the LiteLLM rotator's `kd-embed` group (NIM hosted).
# LLM calls go through `kd-all` / `kd-keylm`. No local models to warm; the
# rotator is constructed lazily on first call. Xinference + its probe block
# removed 2026-05-09 night (see memory: project_local_vs_rotator_architecture).
# =============================================================================
