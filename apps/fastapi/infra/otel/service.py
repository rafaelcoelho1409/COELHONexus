"""OTel SDK bootstrap, exporters, and cross-cutting runtime helpers — the
Imperative Shell for this package. Pure decision logic (the LangFuse span
gate, the baggage-key allow check) lives in `domain.py`; everything here has
a side effect: building resources/exporters, wiring SDK processors, reading
env vars, touching global SDK state, logging.

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
from __future__ import annotations
from . import domain, entities, params

import contextlib
import logging
import os
from collections.abc import Iterator

from opentelemetry import baggage, context, metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GRPCOTLPSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HTTPOTLPSpanExporter,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)


logger = logging.getLogger(__name__)


_otel_initialized: bool = False
_tracer = None
_meter = None
_instruments: dict = {}


# ---------------------------------------------------------------------------
# OTLP export-failure log-spam dampener. The filter itself
# (entities.DedupeRateLimitFilter) is a stateful entity with a real
# invariant, not orchestration — this just wires the shared instance onto
# the noisy loggers.
# ---------------------------------------------------------------------------

def quiet_otel_export_logs() -> None:
    """Idempotent — same filter instance reused so Celery-fork re-init
    never double-adds."""
    for name in params.OTEL_NOISY_LOGGERS:
        lg = logging.getLogger(name)
        if entities.DEDUPE_LOG_FILTER not in lg.filters:
            lg.addFilter(entities.DEDUPE_LOG_FILTER)


# ---------------------------------------------------------------------------
# Resource + exporter builders — Alloy (gRPC) and LangFuse v3 (HTTP). Each is
# best-effort: failure logs + returns False so a flaky backend doesn't block
# init.
# ---------------------------------------------------------------------------

def build_resource() -> Resource:
    attrs: dict = {
        "service.name": os.environ.get(
            "OTEL_SERVICE_NAME", params.SERVICE_NAME_DEFAULT,
        ),
        "service.version": os.environ.get(
            "OTEL_SERVICE_VERSION", params.SERVICE_VERSION_DEFAULT,
        ),
        "deployment.environment": os.environ.get(
            "DEPLOYMENT_ENVIRONMENT", params.DEPLOYMENT_ENVIRONMENT_DEFAULT,
        ),
        "service.namespace": params.SERVICE_NAMESPACE,
    }
    git_sha = os.environ.get("GIT_SHA") or os.environ.get("OTEL_GIT_SHA")
    if git_sha:
        attrs["service.git_sha"] = git_sha
    chart_ver = (
        os.environ.get("HELM_CHART_VERSION")
        or os.environ.get("OTEL_HELM_CHART_VERSION")
    )
    if chart_ver:
        attrs["service.helm_chart_version"] = chart_ver
    extra = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
    if extra:
        attrs.update(domain.parse_resource_attributes(extra))
    return Resource.create(attrs)


def _bsp_kwargs() -> dict:
    """Shared BatchSpanProcessor settings from env overrides."""
    return {
        "max_queue_size": int(
            os.environ.get(
                "OTEL_BSP_MAX_QUEUE_SIZE", str(params.BSP_MAX_QUEUE_SIZE_DEFAULT),
            )
        ),
        "max_export_batch_size": int(
            os.environ.get(
                "OTEL_BSP_MAX_EXPORT_BATCH_SIZE",
                str(params.BSP_MAX_EXPORT_BATCH_SIZE_DEFAULT),
            )
        ),
        "schedule_delay_millis": int(
            os.environ.get(
                "OTEL_BSP_SCHEDULE_DELAY", str(params.BSP_SCHEDULE_DELAY_MS_DEFAULT),
            )
        ),
        "export_timeout_millis": int(
            os.environ.get(
                "OTEL_BSP_EXPORT_TIMEOUT", str(params.BSP_EXPORT_TIMEOUT_MS_DEFAULT),
            )
        ),
    }


# These two wrap real network I/O (`on_end`/`export` ultimately ship spans
# over gRPC/HTTP) — unlike entities.DedupeRateLimitFilter, they aren't value
# objects the package manipulates, they're I/O adapters, so they stay here
# rather than in entities.py. Only their pure gate decision lives in
# domain.should_keep_span.

class LangFuseFilterProcessor(SpanProcessor):
    """Processor-side gate; reduces queue pressure before the authoritative
    LangFuseFilterExporter. Decision logic lives in `domain.should_keep_span`."""

    def __init__(self, inner: SpanProcessor) -> None:
        self._inner = inner

    def on_start(self, span, parent_context=None):
        self._inner.on_start(span, parent_context)

    def on_end(self, span) -> None:
        if domain.should_keep_span(span.name or "", dict(span.attributes or {})):
            self._inner.on_end(span)

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


class LangFuseFilterExporter(SpanExporter):
    """Last gate before OTLP/HTTP export; spans not matching the allow-list
    are dropped here. Decision logic lives in `domain.should_keep_span`."""

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans) -> SpanExportResult:
        kept = tuple(
            span for span in spans
            if domain.should_keep_span(span.name or "", dict(span.attributes or {}))
        )
        if not kept:
            return SpanExportResult.SUCCESS
        return self._inner.export(kept)

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        inner_flush = getattr(self._inner, "force_flush", None)
        if callable(inner_flush):
            return bool(inner_flush(timeout_millis))
        return True


def add_alloy_exporter(tracer_provider) -> bool:
    """gRPC OTLP → Alloy → LGTM."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        logger.info(
            "[otel] OTEL_EXPORTER_OTLP_ENDPOINT unset — Alloy export disabled"
        )
        return False
    try:
        exporter = GRPCOTLPSpanExporter(
            endpoint=endpoint,
            insecure=domain.is_insecure_endpoint(endpoint),
            timeout=int(os.environ.get(
                "OTEL_EXPORTER_OTLP_TIMEOUT", str(params.OTLP_TIMEOUT_DEFAULT_S),
            )),
        )
        tracer_provider.add_span_processor(
            BatchSpanProcessor(exporter, **_bsp_kwargs())
        )
        logger.info(f"[otel] Alloy gRPC OTLP exporter attached → {endpoint}")
        return True
    except Exception as e:
        logger.warning(
            f"[otel] failed to attach Alloy exporter "
            f"({type(e).__name__}: {e}); continuing without it"
        )
        return False


def add_langfuse_exporter(tracer_provider) -> bool:
    """HTTP OTLP → LangFuse v3 /api/public/otel. Skipped silently when
    endpoint OR public/secret keys absent."""
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
            "[otel] LANGFUSE_OTLP_ENDPOINT set but LANGFUSE_PUBLIC_KEY / "
            "LANGFUSE_SECRET_KEY missing — skipping LangFuse exporter"
        )
        return False
    try:
        basic = domain.basic_auth_header(pk, sk)
        traces_endpoint = domain.normalize_traces_endpoint(endpoint)
        raw_exporter = HTTPOTLPSpanExporter(
            endpoint=traces_endpoint,
            headers={"Authorization": f"Basic {basic}"},
            # 10s default timed out under heavy LLM volume; rich batches are slow to ingest.
            timeout=int(os.environ.get(
                "LANGFUSE_OTLP_TIMEOUT", str(params.LANGFUSE_OTLP_TIMEOUT_DEFAULT_S),
            )),
        )
        tracer_provider.add_span_processor(
            LangFuseFilterProcessor(
                BatchSpanProcessor(
                    LangFuseFilterExporter(raw_exporter),
                    **_bsp_kwargs(),
                )
            )
        )
        logger.info(
            f"[otel] LangFuse HTTP OTLP exporter attached → {traces_endpoint}"
        )
        return True
    except Exception as e:
        logger.warning(
            f"[otel] failed to attach LangFuse exporter "
            f"({type(e).__name__}: {e}); continuing without it"
        )
        return False


def add_metric_exporter() -> None:
    """gRPC OTLP metrics → Alloy. LangFuse doesn't ingest metrics."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return
    try:
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=endpoint,
                insecure=domain.is_insecure_endpoint(endpoint),
            ),
            export_interval_millis=params.METRIC_EXPORT_INTERVAL_MS,
        )
        provider = MeterProvider(
            resource=build_resource(),
            metric_readers=[reader],
        )
        metrics.set_meter_provider(provider)
        logger.info("[otel] metric exporter attached → Alloy gRPC OTLP")
    except Exception as e:
        logger.warning(
            f"[otel] failed to attach metric exporter "
            f"({type(e).__name__}: {e})"
        )


# ---------------------------------------------------------------------------
# Baggage propagation — stamp cross-cutting identifiers (study_id, channel_id,
# digest_id, tenant, arm_name, session_id, user_id) into the active OTel
# context so every child span auto-inherits them. No kwargs threading through
# 10 layers.
#
# Pattern:
#     with bag_context(study_id="abc", framework="claude-code"):
#         # every span inside the block carries `study_id` + `framework` attrs
#         await run_pipeline()
#
# `BaggageSpanProcessor` (attached in `init_otel()`) is what mirrors baggage
# entries onto span attributes. Without it baggage still propagates through
# context but won't appear on spans.
# ---------------------------------------------------------------------------

def get_baggage_processor():
    """Return a BaggageSpanProcessor mirroring keys.ALLOWED_BAGGAGE_KEYS
    onto spans.

    Returns None if the optional `opentelemetry-processor-baggage` package
    isn't installed — init_otel() then proceeds without it (baggage still
    propagates in context, just not onto spans)."""
    try:
        from opentelemetry.processor.baggage import BaggageSpanProcessor
    except Exception as e:
        logger.warning(
            f"[otel] BaggageSpanProcessor unavailable "
            f"({type(e).__name__}: {e}) — baggage will propagate through "
            "context but won't appear as span attributes"
        )
        return None

    try:
        return BaggageSpanProcessor(domain.is_allowed_baggage_key)
    except Exception as e:
        logger.warning(f"[otel] BaggageSpanProcessor init failed: {e}")
        return None


@contextlib.contextmanager
def bag_context(**kwargs: str | None) -> Iterator[None]:
    """Attach baggage entries for the duration of the `with` block.

    None values are dropped (lets callers pass optional ids without guards).
    Unknown keys (not in keys.ALLOWED_BAGGAGE_KEYS) still propagate but
    won't be mirrored to spans.
    """
    ctx = context.get_current()
    for key, value in kwargs.items():
        if value is None:
            continue
        ctx = baggage.set_baggage(key, str(value), context=ctx)
    token = context.attach(ctx)
    try:
        yield
    finally:
        context.detach(token)


def current_baggage() -> dict[str, str]:
    """Snapshot of baggage entries in the active context (diagnostics)."""
    try:
        return dict(baggage.get_all())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Lazy instrument factory — one OTel instrument per MetricSpec in
# entities.INSTRUMENTS. Idempotent: instruments are created on the first
# `get_instrument(key)` call after `init_otel()` has run.
#
# Domain `record_*` functions call `get_instrument(key)` and dispatch on the
# returned instrument's `.add()` (counters) or `.record()` (histograms). A
# None return means OTel isn't initialized (or the registry failed to build)
# — recorders treat it as a no-op.
# ---------------------------------------------------------------------------

def _ensure_instruments() -> dict:
    if _instruments:
        return _instruments
    try:
        meter = get_meter()
        for spec in entities.INSTRUMENTS:
            kwargs = domain.instrument_kwargs(spec.name, spec.description, spec.unit)
            factory = (meter.create_counter if spec.kind == "counter"
                       else meter.create_histogram)
            _instruments[spec.key] = factory(**kwargs)
        logger.info(f"[otel-metrics] {len(_instruments)} instruments registered")
    except Exception as e:
        logger.warning(f"[otel-metrics] init failed: {type(e).__name__}: {e}")
    return _instruments


def get_instrument(key: str):
    """Return the OTel instrument for `key`, or None if init failed."""
    return _ensure_instruments().get(key)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def _instrument_libraries() -> None:
    """Auto-instrument httpx/logging/celery once per process.
    Redis intentionally excluded: task-queue chatter produces thousands of
    zero-value spans. Celery here covers the PRODUCER side (traceparent
    injection on `.delay()`); workers re-instrument idempotently on consume
    via `init_otel_for_celery_worker`."""
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
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        CeleryInstrumentor().instrument()
    except Exception as e:
        logger.debug(f"[otel] celery instrumentation skipped: {e}")


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
                    excluded_urls=params.FASTAPI_EXCLUDED_URLS,
                )
            except Exception:
                pass
        return True

    try:
        from opentelemetry.sdk.trace import TracerProvider

        quiet_otel_export_logs()

        resource = build_resource()
        tracer_provider = TracerProvider(resource=resource)

        if (bsp := get_baggage_processor()) is not None:
            tracer_provider.add_span_processor(bsp)
            logger.info("[otel] BaggageSpanProcessor attached")

        alloy_ok = add_alloy_exporter(tracer_provider)
        langfuse_ok = add_langfuse_exporter(tracer_provider)

        trace.set_tracer_provider(tracer_provider)
        _tracer = trace.get_tracer(
            params.INSTRUMENTATION_SCOPE_NAME, params.INSTRUMENTATION_SCOPE_VERSION,
        )

        add_metric_exporter()
        _meter = metrics.get_meter(
            params.INSTRUMENTATION_SCOPE_NAME, params.INSTRUMENTATION_SCOPE_VERSION,
        )

        _instrument_libraries()

        if also_instrument_fastapi_app is not None:
            try:
                from opentelemetry.instrumentation.fastapi import (
                    FastAPIInstrumentor,
                )
                FastAPIInstrumentor.instrument_app(
                    also_instrument_fastapi_app,
                    excluded_urls=params.FASTAPI_EXCLUDED_URLS,
                )
                logger.info("[otel] FastAPI app instrumented")
            except Exception as e:
                logger.warning(f"[otel] FastAPI instrumentation failed: {e}")

        _otel_initialized = True
        exporters = domain.active_exporter_names(alloy_ok=alloy_ok, langfuse_ok=langfuse_ok)
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
    return trace.get_tracer(params.INSTRUMENTATION_SCOPE_NOOP_NAME)


def get_meter():
    """Global meter (after init_otel). Falls back to a no-op meter."""
    if _meter is not None:
        return _meter
    return metrics.get_meter(params.INSTRUMENTATION_SCOPE_NOOP_NAME)


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
