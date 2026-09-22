"""LangFuse span attribute setters.

These helpers keep COELHO Nexus on a single OpenTelemetry tracing plane
while still setting the LangFuse-specific attributes needed for rich trace
previews, filtering, and evaluations.

Use on the CURRENT active span, typically the workflow-root span for DD,
YCS, and RR.
"""
from __future__ import annotations

from typing import Any, Mapping

from opentelemetry import trace

from . import domain


def _current_span():
    span = trace.get_current_span()
    if hasattr(span, "is_recording") and not span.is_recording():
        return None
    return span


def set_current_span_langfuse_io(
    *,
    input_data: Any | None = None,
    output_data: Any | None = None,
) -> None:
    """Attach LangFuse-recognized I/O attributes to the active span.

    We stamp BOTH observation-level and trace-level forms on workflow-root
    spans. LangFuse can derive trace previews from the root observation, but
    setting the trace fields directly makes the behavior explicit and stable.
    """
    span = _current_span()
    if span is None:
        return
    try:
        if input_data is not None:
            encoded = domain.json_attr(input_data)
            span.set_attribute("langfuse.observation.input", encoded)
            span.set_attribute("langfuse.trace.input", encoded)
        if output_data is not None:
            encoded = domain.json_attr(output_data)
            span.set_attribute("langfuse.observation.output", encoded)
            span.set_attribute("langfuse.trace.output", encoded)
    except Exception:
        pass


def set_current_span_langfuse_trace_metadata(
    metadata: Mapping[str, Any] | None,
) -> None:
    """Promote selected workflow fields to filterable LangFuse trace metadata."""
    if not metadata:
        return
    span = _current_span()
    if span is None:
        return
    try:
        for key, value in metadata.items():
            if value is None:
                continue
            span.set_attribute(
                f"langfuse.trace.metadata.{domain.metadata_key(key)}",
                domain.metadata_value(value),
            )
    except Exception:
        pass


def set_current_span_langfuse_observation_metadata(
    metadata: Mapping[str, Any] | None,
) -> None:
    """Promote selected fields to filterable LangFuse observation metadata."""
    if not metadata:
        return
    span = _current_span()
    if span is None:
        return
    try:
        for key, value in metadata.items():
            if value is None:
                continue
            span.set_attribute(
                f"langfuse.observation.metadata.{domain.metadata_key(key)}",
                domain.metadata_value(value),
            )
    except Exception:
        pass
