"""OTel SDK bootstrap with dual export — Alloy (gRPC) + LangFuse v3 (HTTP).

`init_otel()` is idempotent. Call once from FastAPI lifespan + once from
Celery `worker_process_init` — each forked worker needs its own provider
because the parent's SDK state doesn't survive fork().

Env vars (init no-ops when OTEL_EXPORTER_OTLP_ENDPOINT is unset):

  OTEL_EXPORTER_OTLP_ENDPOINT   → Alloy gRPC (e.g. http://alloy:4317)
  OTEL_SERVICE_NAME             → default `coelhonexus-fastapi`
  OTEL_SERVICE_VERSION          → default `1.0.0`
  OTEL_RESOURCE_ATTRIBUTES      → comma-separated `k=v` extras
  LANGFUSE_OTLP_ENDPOINT        → LangFuse v3 /api/public/otel
  LANGFUSE_PUBLIC_KEY           → HTTP Basic
  LANGFUSE_SECRET_KEY
"""
from .service import (
    get_meter,
    get_tracer,
    init_otel,
    init_otel_for_celery_worker,
)


__all__ = [
    "get_meter",
    "get_tracer",
    "init_otel",
    "init_otel_for_celery_worker",
]
