"""Bootstrap orchestration + module-singleton accessors. `init_otel()`
is idempotent; called from FastAPI lifespan AND Celery worker_process_init
(each forked worker needs its own provider — parent SDK state doesn't
survive fork())."""
from __future__ import annotations

import logging

from .exporters import (
    add_alloy_exporter,
    add_langfuse_exporter,
    add_metric_exporter,
    build_resource,
)
from .filters import quiet_otel_export_logs


logger = logging.getLogger(__name__)


_otel_initialized: bool = False
_tracer = None
_meter = None


def _instrument_libraries() -> None:
    """Auto-instrument httpx/logging/litellm once per process.
    Redis intentionally excluded: task-queue chatter produces thousands of zero-value spans."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except Exception as e:
        logger.debug(f"[otel] httpx instrumentation skipped: {e}")
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        LoggingInstrumentor().instrument(set_logging_format=True)
    except Exception as e:
        logger.debug(f"[otel] logging instrumentation skipped: {e}")
    # (mutates spans after close → "Setting attribute on ended span"). Cost callback only.
    try:
        import litellm
        from .litellm_callbacks import cost_callback as _cost_cb
        succ_now = list(litellm.success_callback or [])
        if _cost_cb not in succ_now:
            litellm.success_callback = succ_now + [_cost_cb]
        logger.info(
            "[otel] litellm callbacks wired: cost_callback (fn)"
        )
    except Exception as e:
        logger.debug(f"[otel] litellm callback wiring skipped: {e}")


def init_otel(also_instrument_fastapi_app=None) -> bool:
    """Bootstrap SDK with dual export. Idempotent. `also_instrument_fastapi_app`
    is the FastAPI() instance to auto-instrument (passed from lifespan AFTER
    the app is built; Celery workers skip it)."""
    global _otel_initialized, _tracer, _meter

    if _otel_initialized:
        if also_instrument_fastapi_app is not None:
            try:
                from opentelemetry.instrumentation.fastapi import (
                    FastAPIInstrumentor,
                )
                FastAPIInstrumentor.instrument_app(
                    also_instrument_fastapi_app,
                    excluded_urls=(
                        "health,healthz,readyz,livez,ready,live,"
                        "metrics,docs,redoc,openapi,favicon"
                    ),
                )
            except Exception:
                pass
        return True

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        quiet_otel_export_logs()

        resource = build_resource()
        tracer_provider = TracerProvider(resource=resource)

        from .baggage import get_baggage_processor
        if (bsp := get_baggage_processor()) is not None:
            tracer_provider.add_span_processor(bsp)
            logger.info("[otel] BaggageSpanProcessor attached")

        alloy_ok = add_alloy_exporter(tracer_provider)
        langfuse_ok = add_langfuse_exporter(tracer_provider)

        trace.set_tracer_provider(tracer_provider)
        _tracer = trace.get_tracer("coelhonexus.fastapi", "1.0.0")

        add_metric_exporter()
        from opentelemetry import metrics as otel_metrics
        _meter = otel_metrics.get_meter("coelhonexus.fastapi", "1.0.0")

        _instrument_libraries()

        if also_instrument_fastapi_app is not None:
            try:
                from opentelemetry.instrumentation.fastapi import (
                    FastAPIInstrumentor,
                )
                FastAPIInstrumentor.instrument_app(
                    also_instrument_fastapi_app,
                    excluded_urls=(
                        "health,healthz,readyz,livez,ready,live,"
                        "metrics,docs,redoc,openapi,favicon"
                    ),
                )
                logger.info("[otel] FastAPI app instrumented")
            except Exception as e:
                logger.warning(f"[otel] FastAPI instrumentation failed: {e}")

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
    return trace.get_tracer("coelhonexus.fastapi.noop")


def get_meter():
    """Global meter (after init_otel). Falls back to a no-op meter."""
    if _meter is not None:
        return _meter
    from opentelemetry import metrics
    return metrics.get_meter("coelhonexus.fastapi.noop")


def init_otel_for_celery_worker() -> bool:
    """Celery `worker_process_init` handler. Init OTel + auto-instrument
    Celery tasks (study_id correlation in trace context)."""
    ok = init_otel(also_instrument_fastapi_app=None)
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        CeleryInstrumentor().instrument()
        logger.info("[otel] Celery instrumentation attached (worker)")
    except Exception as e:
        logger.debug(f"[otel] Celery instrumentation skipped: {e}")
    return ok
