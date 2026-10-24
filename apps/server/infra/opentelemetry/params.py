"""OTel SDK env and tunables — endpoint, resource identity, backpressure, intervals, defaults, noisy loggers.

Soft env reads: with `OTLP_ENDPOINT` unset the exporters and `init_otel` no-op."""
from __future__ import annotations

import os

SERVICE_NAME_DEFAULT = "coelhonexus-server"
SERVICE_VERSION_DEFAULT = "1.0.0"
DEPLOYMENT_ENVIRONMENT_DEFAULT = "dev"
SERVICE_NAMESPACE = "coelhonexus"


# Alloy gRPC endpoint (e.g. http://alloy:4317) — unset disables export.
OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")

# Resource identity of this process (Tempo / Mimir labels).
SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", SERVICE_NAME_DEFAULT)
SERVICE_VERSION = os.environ.get("OTEL_SERVICE_VERSION", SERVICE_VERSION_DEFAULT)
DEPLOYMENT_ENVIRONMENT = os.environ.get("DEPLOYMENT_ENVIRONMENT", DEPLOYMENT_ENVIRONMENT_DEFAULT)
GIT_SHA = os.environ.get("GIT_SHA") or os.environ.get("OTEL_GIT_SHA") or ""
HELM_CHART_VERSION = os.environ.get("HELM_CHART_VERSION") or os.environ.get("OTEL_HELM_CHART_VERSION") or ""
RESOURCE_ATTRIBUTES = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")   # comma-separated `k=v` extras


# Identity for this app's OWN tracer/meter (`get_tracer(name, version)` /
# `get_meter(name, version)`) — distinct from SERVICE_VERSION_DEFAULT above,
# which describes the deployed app. These describe the instrumenting code
# itself; they happen to share "1.0.0" today but must not be collapsed into
# one constant, or a future app-version bump would silently change this too.
INSTRUMENTATION_SCOPE_NAME = "coelhonexus.server"
INSTRUMENTATION_SCOPE_NOOP_NAME = "coelhonexus.server.noop"
INSTRUMENTATION_SCOPE_VERSION = "1.0.0"


OTLP_TIMEOUT_DEFAULT_S = 30
OTLP_TIMEOUT_S = int(os.environ.get("OTEL_EXPORTER_OTLP_TIMEOUT", str(OTLP_TIMEOUT_DEFAULT_S)))


# Phase E : SDK defaults (q=2048, b=512, d=5s) caused Alloy
# RESOURCE_EXHAUSTED under heavy LangChain Planner volume. Triple queue +
# halve batch + double delay so bursts buffer locally and drain in chunks.
# The `OTEL_BSP_*` env vars override each.
BSP_MAX_QUEUE_SIZE_DEFAULT = 6144
BSP_MAX_EXPORT_BATCH_SIZE_DEFAULT = 256
BSP_SCHEDULE_DELAY_MS_DEFAULT = 10_000
BSP_EXPORT_TIMEOUT_MS_DEFAULT = 30_000
BSP_MAX_QUEUE_SIZE = int(os.environ.get("OTEL_BSP_MAX_QUEUE_SIZE", str(BSP_MAX_QUEUE_SIZE_DEFAULT)))
BSP_MAX_EXPORT_BATCH_SIZE = int(os.environ.get("OTEL_BSP_MAX_EXPORT_BATCH_SIZE", str(BSP_MAX_EXPORT_BATCH_SIZE_DEFAULT)))
BSP_SCHEDULE_DELAY_MS = int(os.environ.get("OTEL_BSP_SCHEDULE_DELAY", str(BSP_SCHEDULE_DELAY_MS_DEFAULT)))
BSP_EXPORT_TIMEOUT_MS = int(os.environ.get("OTEL_BSP_EXPORT_TIMEOUT", str(BSP_EXPORT_TIMEOUT_MS_DEFAULT)))


METRIC_EXPORT_INTERVAL_MS = 15_000


# FastAPIInstrumentor.instrument_app(excluded_urls=...) — was duplicated
# verbatim in two branches of init_otel(); a single constant means the two
# can't drift out of sync.
FASTAPI_EXCLUDED_URLS = (
    "health,healthz,readyz,livez,ready,live,metrics,docs,redoc,openapi,favicon"
)


# 5-min rate-limit on OTLP export-failure spam — first WARN through (signal
# that telemetry degraded), then suppress until the window expires.
DEDUPE_LOG_INTERVAL_S = 300.0


# logging.Filter doesn't cascade to child loggers, so we attach per-leaf
# by name (absent names get created lazily — harmless). Stays here (not
# keys.py) because it's consumed only by service.py's own
# quiet_otel_export_logs(), never from another module.
OTEL_NOISY_LOGGERS = (
    "opentelemetry.exporter.otlp.proto.grpc.exporter",
    "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
    "opentelemetry.exporter.otlp.proto.grpc.metric_exporter",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    "opentelemetry.sdk.trace.export",
)
