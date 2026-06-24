"""Resource + exporter builders for Alloy (gRPC) and LangFuse v3 (HTTP).

Each is best-effort: failure logs + returns False so a flaky backend doesn't
block init. Ported from apps/fastapi/infra/otel/exporters.py with the
service-name resolution adapted for the peer-app override pattern.
"""
from __future__ import annotations

import base64
import logging
import os

from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GRPCOTLPSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HTTPOTLPSpanExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from .params import (
    BSP_EXPORT_TIMEOUT_MS_DEFAULT,
    BSP_MAX_EXPORT_BATCH_SIZE_DEFAULT,
    BSP_MAX_QUEUE_SIZE_DEFAULT,
    BSP_SCHEDULE_DELAY_MS_DEFAULT,
    DEPLOYMENT_ENVIRONMENT_DEFAULT,
    LANGFUSE_OTLP_TIMEOUT_DEFAULT_S,
    OTLP_TIMEOUT_DEFAULT_S,
    SERVICE_NAME_DEFAULT,
    SERVICE_NAMESPACE,
    SERVICE_VERSION_DEFAULT,
)


logger = logging.getLogger(__name__)


class _LangFuseSpanGate:
    """Default-deny allow-list for the LangFuse export arm.

    Only spans explicitly in an allow category reach LangFuse. Everything
    else (HTTP instrumentation, Redis chatter, Celery internals, health
    probes, unknown spans) is dropped at the processor and exporter.

    Allow categories:
      - `coelho.langfuse.keep = True` attribute set by domain code
      - `gen_ai.*` attributes present (LiteLLM / rotator LLM call spans)
      - span name starts with a domain prefix: dd. rr. ycs. mcp.tool. rotator.
      - Celery task root spans for domain tasks (run/domains.{dd,rr,ycs}.)
        — these become the trace root in LangFuse and give the trace its name

    Explicit deny (evaluated before allow, blocks MCP transport noise):
      - `POST /mcp*`, `GET /mcp*`, `DELETE /mcp*` — httpx client spans from
        the MCP client making requests to the FastMCP server
      - `tools/list`, `tools/call*` — JSON-RPC protocol-level spans
    """

    _CELERY_DOMAIN_PREFIXES: tuple[str, ...] = (
        "run/domains.dd.",
        "run/domains.rr.",
        "run/domains.ycs.",
    )

    _MCP_TRANSPORT_DROPS: tuple[str, ...] = (
        "POST /mcp",
        "GET /mcp",
        "DELETE /mcp",
        "tools/list",
        "tools/call",
    )

    def _attrs(self, span) -> dict:
        return dict(span.attributes or {})

    def _has_genai_attrs(self, attrs: dict) -> bool:
        return any(str(k).startswith("gen_ai.") for k in attrs.keys())

    def _is_explicit_drop(self, span) -> bool:
        name = span.name or ""
        return any(name.startswith(p) for p in self._MCP_TRANSPORT_DROPS)

    def _is_curated_keep(self, span, attrs: dict) -> bool:
        if attrs.get("coelho.langfuse.keep") is True:
            return True
        name = span.name or ""
        return (
            name.startswith("dd.")
            or name.startswith("rr.")
            or name.startswith("ycs.")
            or name.startswith("mcp.tool.")
            or name.startswith("rotator.")
            or any(name.startswith(p) for p in self._CELERY_DOMAIN_PREFIXES)
        )

    def should_keep(self, span) -> bool:
        if self._is_explicit_drop(span):
            return False
        attrs = self._attrs(span)
        return self._is_curated_keep(span, attrs) or self._has_genai_attrs(attrs)


class LangFuseFilterProcessor(SpanProcessor):
    """Processor-side gate kept for in-process short-circuiting."""

    _gate = _LangFuseSpanGate()

    def __init__(self, inner: SpanProcessor) -> None:
        self._inner = inner

    def on_start(self, span, parent_context=None):
        self._inner.on_start(span, parent_context)

    def on_end(self, span) -> None:
        if self._gate.should_keep(span):
            self._inner.on_end(span)

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


class LangFuseFilterExporter(SpanExporter):
    """Exporter-side LangFuse allow-list.

    This is the reliable last gate before OTLP/HTTP export.
    """

    _gate = _LangFuseSpanGate()

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans) -> SpanExportResult:
        kept = tuple(span for span in spans if self._gate.should_keep(span))
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


def _resolve_service_name() -> str:
    """Peer-app override pattern.

    The shared `coelhonexus.commonEnvVars` Helm helper sets
    `OTEL_SERVICE_NAME=coelhonexus-fastapi` for *every* pod (it predates the
    multi-peer-app split). To avoid fastmcp spans being misattributed to
    fastapi in Tempo/LangFuse we check `OTEL_FASTMCP_SERVICE_NAME` first
    (set in this app's configmap). Falls back through the shared env to the
    hardcoded `coelhonexus-fastmcp` default — so traces are correctly named
    even if the configmap forgets the override.
    """
    return (
        os.environ.get("OTEL_FASTMCP_SERVICE_NAME")
        or os.environ.get("OTEL_SERVICE_NAME")
        or SERVICE_NAME_DEFAULT
    )


def build_resource() -> Resource:
    attrs: dict = {
        "service.name": _resolve_service_name(),
        "service.version": os.environ.get(
            "OTEL_SERVICE_VERSION", SERVICE_VERSION_DEFAULT,
        ),
        "deployment.environment": os.environ.get(
            "DEPLOYMENT_ENVIRONMENT", DEPLOYMENT_ENVIRONMENT_DEFAULT,
        ),
        "service.namespace": SERVICE_NAMESPACE,
    }
    extra = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
    if extra:
        for pair in extra.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                # Don't let `OTEL_RESOURCE_ATTRIBUTES` overwrite the resolved
                # service.name — it's set by commonEnvVars to fastapi's name.
                if k.strip() == "service.name":
                    continue
                attrs[k.strip()] = v.strip()
    return Resource.create(attrs)


def _bsp_kwargs() -> dict:
    """Shared BatchSpanProcessor settings — see params.py Phase E rationale."""
    return {
        "max_queue_size": int(os.environ.get(
            "OTEL_BSP_MAX_QUEUE_SIZE", str(BSP_MAX_QUEUE_SIZE_DEFAULT),
        )),
        "max_export_batch_size": int(os.environ.get(
            "OTEL_BSP_MAX_EXPORT_BATCH_SIZE",
            str(BSP_MAX_EXPORT_BATCH_SIZE_DEFAULT),
        )),
        "schedule_delay_millis": int(os.environ.get(
            "OTEL_BSP_SCHEDULE_DELAY", str(BSP_SCHEDULE_DELAY_MS_DEFAULT),
        )),
        "export_timeout_millis": int(os.environ.get(
            "OTEL_BSP_EXPORT_TIMEOUT", str(BSP_EXPORT_TIMEOUT_MS_DEFAULT),
        )),
    }


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
            insecure=endpoint.startswith("http://"),
            timeout=int(os.environ.get(
                "OTEL_EXPORTER_OTLP_TIMEOUT", str(OTLP_TIMEOUT_DEFAULT_S),
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
        basic = base64.b64encode(f"{pk}:{sk}".encode()).decode()
        # LangFuse expects `/api/public/otel/v1/traces`; append if the
        # operator gave just the base.
        traces_endpoint = endpoint.rstrip("/")
        if not traces_endpoint.endswith("/v1/traces"):
            traces_endpoint = f"{traces_endpoint}/v1/traces"
        raw_exporter = HTTPOTLPSpanExporter(
            endpoint=traces_endpoint,
            headers={"Authorization": f"Basic {basic}"},
            timeout=int(os.environ.get(
                "LANGFUSE_OTLP_TIMEOUT", str(LANGFUSE_OTLP_TIMEOUT_DEFAULT_S),
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
