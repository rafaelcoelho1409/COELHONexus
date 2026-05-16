"""
OpenTelemetry bootstrap — dual-export to LGTM stack (via Alloy) + LangFuse v3.

Design (2026-05-12 night):
  - ONE OTel SDK initialization emits spans/metrics/logs into a single pipeline.
  - The pipeline fans out via TWO BatchSpanProcessor instances:
      1. gRPC OTLP → Alloy receiver (otlp/v1/traces on :4317)
         → Alloy routes: metrics → Mimir, traces → Tempo, logs → Loki
      2. HTTP OTLP → LangFuse v3 public API `/api/public/otel` endpoint
         → LangFuse renders per-prompt traces with rich LLM-specific UI
  - LiteLLM's `callbacks=["otel"]` integration ALSO emits to the same TracerProvider,
    so per-deployment latency / success / token / cost data lands in both backends
    without any per-call code change.

Critical for Celery prefork workers:
  - `init_otel()` is called from BOTH FastAPI's lifespan startup AND Celery's
    `worker_process_init` signal. Each forked worker has its own event loop +
    process — they need their own TracerProvider/MeterProvider/BatchSpanProcessor
    threads. The parent's OTel state does NOT survive fork() cleanly.

Env vars consumed (all optional — init is a no-op if OTEL_EXPORTER_OTLP_ENDPOINT
                    is unset, which is the safest default in dev):

  OTEL_EXPORTER_OTLP_ENDPOINT   → e.g. `http://alloy.monitoring.svc.cluster.local:4317`
                                  Used by the gRPC exporter targeting Alloy.
  OTEL_SERVICE_NAME             → e.g. `coelhonexus-fastapi` (default)
  OTEL_RESOURCE_ATTRIBUTES      → e.g. `deployment.environment=dev,service.namespace=kd`
  OTEL_SERVICE_VERSION          → e.g. `1.0.0` (read from pyproject)
  LANGFUSE_OTLP_ENDPOINT        → e.g. `http://langfuse-web.langfuse.svc.cluster.local:3000/api/public/otel`
                                  LangFuse v3 OTLP HTTP endpoint. If unset, the
                                  LangFuse exporter is skipped (Alloy-only mode).
  LANGFUSE_PUBLIC_KEY           → API public key (used in HTTP Basic auth)
  LANGFUSE_SECRET_KEY           → API secret key

Idempotent: calling init_otel() multiple times in the same process is safe.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level guard so multiple init calls per process are no-ops after the first.
_otel_initialized: bool = False
_tracer = None
_meter = None


def _build_resource():
    """Build the OTel Resource (service.name, version, deployment.environment, etc.)."""
    from opentelemetry.sdk.resources import Resource

    attrs: dict = {
        "service.name": os.environ.get("OTEL_SERVICE_NAME", "coelhonexus-fastapi"),
        "service.version": os.environ.get("OTEL_SERVICE_VERSION", "1.0.0"),
        "deployment.environment": os.environ.get(
            "DEPLOYMENT_ENVIRONMENT", "dev",
        ),
        "service.namespace": "knowledge-distiller",
    }
    # Allow operator overrides via OTEL_RESOURCE_ATTRIBUTES="k1=v1,k2=v2"
    extra = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
    if extra:
        for pair in extra.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                attrs[k.strip()] = v.strip()
    return Resource.create(attrs)


def _add_alloy_exporter(tracer_provider) -> bool:
    """Attach the gRPC OTLP exporter that sends to Alloy → LGTM. Returns True if added."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        logger.info("[otel] OTEL_EXPORTER_OTLP_ENDPOINT unset — Alloy export disabled")
        return False
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            insecure=endpoint.startswith("http://"),
        )
        tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info(f"[otel] Alloy gRPC OTLP exporter attached → {endpoint}")
        return True
    except Exception as e:
        logger.warning(
            f"[otel] failed to attach Alloy exporter "
            f"({type(e).__name__}: {e}); continuing without it"
        )
        return False


def _add_langfuse_exporter(tracer_provider) -> bool:
    """Attach the HTTP OTLP exporter that sends to LangFuse v3. Returns True if added."""
    endpoint = os.environ.get("LANGFUSE_OTLP_ENDPOINT")
    if not endpoint:
        logger.info(
            "[otel] LANGFUSE_OTLP_ENDPOINT unset — LangFuse export disabled "
            "(LGTM-only mode)"
        )
        return False
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sk = os.environ.get("LANGFUSE_SECRET_KEY")
    if not (pk and sk):
        logger.warning(
            "[otel] LANGFUSE_OTLP_ENDPOINT set but LANGFUSE_PUBLIC_KEY/SECRET_KEY "
            "missing — skipping LangFuse exporter"
        )
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HTTPOTLPSpanExporter,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # LangFuse v3 uses HTTP Basic auth: Authorization: Basic <b64(pk:sk)>
        basic = base64.b64encode(f"{pk}:{sk}".encode()).decode()
        # Normalize endpoint — LangFuse expects `/api/public/otel/v1/traces`;
        # if user gave just the base `/api/public/otel`, append the spec'd path.
        traces_endpoint = endpoint.rstrip("/")
        if not traces_endpoint.endswith("/v1/traces"):
            traces_endpoint = f"{traces_endpoint}/v1/traces"
        exporter = HTTPOTLPSpanExporter(
            endpoint=traces_endpoint,
            headers={"Authorization": f"Basic {basic}"},
        )
        tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info(f"[otel] LangFuse HTTP OTLP exporter attached → {traces_endpoint}")
        return True
    except Exception as e:
        logger.warning(
            f"[otel] failed to attach LangFuse exporter "
            f"({type(e).__name__}: {e}); continuing without it"
        )
        return False


def _add_metric_exporter() -> None:
    """Configure metric export to Alloy via gRPC OTLP. LangFuse doesn't take metrics."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return
    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry import metrics as otel_metrics

        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=endpoint,
                insecure=endpoint.startswith("http://"),
            ),
            export_interval_millis=15_000,  # 15s — good balance for Mimir scrape
        )
        provider = MeterProvider(
            resource=_build_resource(),
            metric_readers=[reader],
        )
        otel_metrics.set_meter_provider(provider)
        logger.info("[otel] metric exporter attached → Alloy gRPC OTLP")
    except Exception as e:
        logger.warning(
            f"[otel] failed to attach metric exporter "
            f"({type(e).__name__}: {e})"
        )


def _instrument_libraries() -> None:
    """Auto-instrument FastAPI, httpx, redis, Celery, logging — once per process."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except Exception as e:
        logger.debug(f"[otel] httpx instrumentation skipped: {e}")
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        RedisInstrumentor().instrument()
    except Exception as e:
        logger.debug(f"[otel] redis instrumentation skipped: {e}")
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        # Inject trace_id + span_id into log records — Loki can then correlate
        # log lines with traces in Tempo. `set_logging_format=False` because we
        # control the formatter elsewhere.
        LoggingInstrumentor().instrument(set_logging_format=False)
    except Exception as e:
        logger.debug(f"[otel] logging instrumentation skipped: {e}")


def init_otel(also_instrument_fastapi_app=None) -> bool:
    """
    Bootstrap the OTel SDK with dual export (Alloy + LangFuse).

    Idempotent — safe to call multiple times per process. Returns True if the
    init ran or has already run; False on hard failure.

    Args:
        also_instrument_fastapi_app: optional FastAPI() instance to auto-
            instrument. Pass from app.py lifespan AFTER app is fully constructed.
            Celery workers skip this (no FastAPI app in the worker process).
    """
    global _otel_initialized, _tracer, _meter

    if _otel_initialized:
        # Even on re-call, auto-instrument FastAPI if it wasn't done before.
        if also_instrument_fastapi_app is not None:
            try:
                from opentelemetry.instrumentation.fastapi import (
                    FastAPIInstrumentor,
                )
                FastAPIInstrumentor.instrument_app(also_instrument_fastapi_app)
            except Exception:
                pass
        return True

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        resource = _build_resource()
        tracer_provider = TracerProvider(resource=resource)

        # Dual span export: Alloy (gRPC) + LangFuse v3 (HTTP). At least ONE must
        # succeed; otherwise we still install the provider with no exporters
        # (spans go nowhere but the API remains usable for code that calls
        # tracer.start_as_current_span()).
        alloy_ok = _add_alloy_exporter(tracer_provider)
        langfuse_ok = _add_langfuse_exporter(tracer_provider)

        trace.set_tracer_provider(tracer_provider)
        _tracer = trace.get_tracer("kd.fastapi", "1.0.0")

        # Metrics — only to Alloy (LangFuse doesn't ingest metrics).
        _add_metric_exporter()
        from opentelemetry import metrics as otel_metrics
        _meter = otel_metrics.get_meter("kd.fastapi", "1.0.0")

        # Auto-instrument libs (httpx, redis, logging).
        _instrument_libraries()

        # FastAPI app instrumentation — caller-supplied.
        if also_instrument_fastapi_app is not None:
            try:
                from opentelemetry.instrumentation.fastapi import (
                    FastAPIInstrumentor,
                )
                FastAPIInstrumentor.instrument_app(also_instrument_fastapi_app)
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
    """Return the global tracer (after init_otel). Falls back to a no-op tracer."""
    if _tracer is not None:
        return _tracer
    from opentelemetry import trace
    return trace.get_tracer("kd.fastapi.noop")


def get_meter():
    """Return the global meter (after init_otel). Falls back to a no-op meter."""
    if _meter is not None:
        return _meter
    from opentelemetry import metrics
    return metrics.get_meter("kd.fastapi.noop")


def init_otel_for_celery_worker() -> bool:
    """
    Celery `worker_process_init` signal handler. Initializes OTel in each
    forked worker process. Auto-instruments Celery tasks (study_id correlation
    in trace context).
    """
    ok = init_otel(also_instrument_fastapi_app=None)
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        CeleryInstrumentor().instrument()
        logger.info("[otel] Celery instrumentation attached (worker)")
    except Exception as e:
        logger.debug(f"[otel] Celery instrumentation skipped: {e}")
    return ok
