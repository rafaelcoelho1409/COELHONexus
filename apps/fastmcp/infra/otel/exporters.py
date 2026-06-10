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
from opentelemetry.sdk.trace.export import BatchSpanProcessor

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
        exporter = HTTPOTLPSpanExporter(
            endpoint=traces_endpoint,
            headers={"Authorization": f"Basic {basic}"},
            timeout=int(os.environ.get(
                "LANGFUSE_OTLP_TIMEOUT", str(LANGFUSE_OTLP_TIMEOUT_DEFAULT_S),
            )),
        )
        tracer_provider.add_span_processor(
            BatchSpanProcessor(exporter, **_bsp_kwargs())
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
