"""OTel SDK tunables — backpressure, intervals, defaults, noisy loggers.

Mirrors apps/fastapi/infra/otel/params.py. Same Phase E (2026-05-23)
BatchSpanProcessor backpressure tuning carries over because the same Alloy
collector receives this peer app's spans.
"""
from __future__ import annotations


SERVICE_NAME_DEFAULT = "coelhonexus-fastmcp"
SERVICE_VERSION_DEFAULT = "1.0.0"
DEPLOYMENT_ENVIRONMENT_DEFAULT = "dev"
SERVICE_NAMESPACE = "research-radar"


OTLP_TIMEOUT_DEFAULT_S = 30
LANGFUSE_OTLP_TIMEOUT_DEFAULT_S = 30


# Phase E (2026-05-23): SDK defaults (q=2048, b=512, d=5s) caused Alloy
# RESOURCE_EXHAUSTED under heavy LangChain Planner volume. Triple queue +
# halve batch + double delay so bursts buffer locally and drain in chunks.
# fastmcp volume is much lower than fastapi (no LangChain — just tool calls),
# but matching the fastapi tuning keeps Alloy backpressure uniform.
BSP_MAX_QUEUE_SIZE_DEFAULT = 6144
BSP_MAX_EXPORT_BATCH_SIZE_DEFAULT = 256
BSP_SCHEDULE_DELAY_MS_DEFAULT = 10_000
BSP_EXPORT_TIMEOUT_MS_DEFAULT = 30_000


# 5-min rate-limit on OTLP export-failure spam — first WARN through (signal
# that telemetry degraded), then suppress until the window expires.
DEDUPE_LOG_INTERVAL_S = 300.0


# logging.Filter doesn't cascade to child loggers, so we attach per-leaf by
# name (absent names get created lazily — harmless).
OTEL_NOISY_LOGGERS = (
    "opentelemetry.exporter.otlp.proto.grpc.exporter",
    "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    "opentelemetry.sdk.trace.export",
)
