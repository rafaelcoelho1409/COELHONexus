"""OTel SDK bootstrap with dual export — Alloy (gRPC) + LangFuse v3 (HTTP).

Ported from apps/fastapi/infra/otel/ (same structure: params / exporters /
filters / service). Simplified for the fastmcp peer app:
  - No FastAPI instrumentation (this peer app uses Starlette directly via
    fastmcp.http_app(); auto-instrumentation is added at the Starlette level
    by httpx + logging instrumentors below).
  - No Celery worker init (no Celery in fastmcp).

`init_otel()` is idempotent. Call once from `apps/fastmcp/server.py` module
import — uvicorn workers each get their own provider.

Env vars (init no-ops when OTEL_EXPORTER_OTLP_ENDPOINT is unset):

  OTEL_EXPORTER_OTLP_ENDPOINT   → Alloy gRPC (e.g. http://alloy:4317)
  OTEL_FASTMCP_SERVICE_NAME     → override per-peer-app (else OTEL_SERVICE_NAME → default)
  OTEL_SERVICE_NAME             → inherited from coelhonexus.commonEnvVars
  OTEL_SERVICE_VERSION          → default `1.0.0`
  OTEL_RESOURCE_ATTRIBUTES      → comma-separated `k=v` extras
  LANGFUSE_OTLP_ENDPOINT        → LangFuse v3 /api/public/otel
  LANGFUSE_PUBLIC_KEY           → HTTP Basic
  LANGFUSE_SECRET_KEY
"""
from .service import get_tracer, init_otel


__all__ = ["get_tracer", "init_otel"]
