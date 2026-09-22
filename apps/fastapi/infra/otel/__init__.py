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

  domain.py    — pure span-gate + baggage-key predicates
  entities.py  — MetricSpec registry (INSTRUMENTS) + DedupeRateLimitFilter
  keys.py      — task-path / route-name tables, baggage-key allow-list
  params.py    — tunables (service identity, timeouts, backpressure)
  service.py   — bootstrap, exporters, baggage wiring, instrument factory
"""
from . import domain, entities, keys, params, service

__all__ = [
    "domain",
    "entities",
    "keys",
    "params",
    "service",
]
