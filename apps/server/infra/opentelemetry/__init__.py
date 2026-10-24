"""OTel SDK bootstrap with dual export — Alloy (gRPC) + LangFuse v3 (HTTP).

`init_otel()` is idempotent. Call once from FastAPI lifespan + once from
Celery `worker_process_init` — each forked worker needs its own provider
because the parent's SDK state doesn't survive fork().

Env vars (init no-ops when OTEL_EXPORTER_OTLP_ENDPOINT is unset):

  OTEL_EXPORTER_OTLP_ENDPOINT   → Alloy gRPC (e.g. http://alloy:4317)
  OTEL_SERVICE_NAME             → default `coelhonexus-server`
  OTEL_SERVICE_VERSION          → default `1.0.0`
  OTEL_RESOURCE_ATTRIBUTES      → comma-separated `k=v` extras

LangFuse export (LANGFUSE_OTLP_ENDPOINT / PUBLIC_KEY / SECRET_KEY) is owned
by `infra.langfuse.service.add_exporter`.

  domain.py    — pure baggage-key predicate + string transforms
  entities.py  — MetricSpec registry (INSTRUMENTS) + DedupeRateLimitFilter
  keys.py      — baggage-key allow-list
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
