"""Bootstrap orchestration + module-singleton accessors.

Ported from apps/fastapi/infra/otel/service.py, trimmed for the fastmcp
peer app:
  - No FastAPI app instrumentation (this is Starlette via fastmcp.http_app()).
  - No Celery worker init (no Celery here).
  - Keeps httpx + logging instrumentation (the source tools all use httpx;
    trace_id correlation in logs lets Loki ↔ Tempo cross-jump).

`init_otel()` is idempotent — called once at module import in
apps/fastmcp/server.py.
"""
from __future__ import annotations

import logging

from .exporters import (
    add_alloy_exporter,
    add_langfuse_exporter,
    build_resource,
)
from .filters import quiet_otel_export_logs


logger = logging.getLogger(__name__)


_otel_initialized: bool = False
_tracer = None


def _instrument_libraries() -> None:
    """Auto-instrument httpx + logging — once per process."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except Exception as e:
        logger.debug(f"[otel] httpx instrumentation skipped: {e}")
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        # Inject trace_id + span_id into log records so Loki ↔ Tempo can
        # correlate. set_logging_format=False — formatter set elsewhere.
        LoggingInstrumentor().instrument(set_logging_format=False)
    except Exception as e:
        logger.debug(f"[otel] logging instrumentation skipped: {e}")


def init_otel() -> bool:
    """Bootstrap SDK with dual export. Idempotent — safe to call from module
    import. Returns True on success (even if both exporters are disabled —
    spans then go to a no-op pipeline)."""
    global _otel_initialized, _tracer

    if _otel_initialized:
        return True

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        # Attach log filter BEFORE exporters so the first failed export is
        # already rate-limited (collector-down at startup is common).
        quiet_otel_export_logs()

        resource = build_resource()
        tracer_provider = TracerProvider(resource=resource)

        # At least one SHOULD attach; if neither does the provider still
        # installs (spans go nowhere but tracer.start_as_current_span works).
        alloy_ok = add_alloy_exporter(tracer_provider)
        langfuse_ok = add_langfuse_exporter(tracer_provider)

        trace.set_tracer_provider(tracer_provider)
        _tracer = trace.get_tracer("coelhonexus.fastmcp", "1.0.0")

        _instrument_libraries()

        _otel_initialized = True
        exporters = []
        if alloy_ok:
            exporters.append("alloy")
        if langfuse_ok:
            exporters.append("langfuse")
        logger.info(
            f"[otel] initialized — exporters=[{', '.join(exporters) or 'none'}]"
        )
        return True
    except Exception as e:
        logger.exception(f"[otel] init failed: {type(e).__name__}: {e}")
        return False


def get_tracer():
    """Global tracer (after init_otel). Falls back to a no-op tracer."""
    if _tracer is not None:
        return _tracer
    from opentelemetry import trace
    return trace.get_tracer("coelhonexus.fastmcp.noop")
