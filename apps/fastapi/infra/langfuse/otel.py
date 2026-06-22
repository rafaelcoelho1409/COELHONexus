"""LangFuse OTEL attribute helpers.

These helpers keep COELHO Nexus on a single OpenTelemetry tracing plane
while still setting the LangFuse-specific attributes needed for rich trace
previews, filtering, and evaluations.

Use on the CURRENT active span, typically the workflow-root span for DD,
YCS, and RR.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from opentelemetry import trace as _otel_trace


_JSON_ATTR_CAP = 12_000
_METADATA_VALUE_CAP = 512
_SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _current_span():
    span = _otel_trace.get_current_span()
    if hasattr(span, "is_recording") and not span.is_recording():
        return None
    return span


def _truncate(text: str, cap: int) -> str:
    if cap <= 0 or len(text) <= cap:
        return text
    return text[:cap] + f"…+{len(text) - cap}b"


def _json_attr(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        raw = json.dumps(str(value), ensure_ascii=False)
    return _truncate(raw, _JSON_ATTR_CAP)


def _metadata_key(key: str) -> str:
    safe = _SAFE_KEY_RE.sub("_", str(key).strip())
    return safe.strip("._-") or "value"


def _metadata_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _truncate(value, _METADATA_VALUE_CAP)
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        raw = str(value)
    return _truncate(raw, _METADATA_VALUE_CAP)


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
            encoded = _json_attr(input_data)
            span.set_attribute("langfuse.observation.input", encoded)
            span.set_attribute("langfuse.trace.input", encoded)
        if output_data is not None:
            encoded = _json_attr(output_data)
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
                f"langfuse.trace.metadata.{_metadata_key(key)}",
                _metadata_value(value),
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
                f"langfuse.observation.metadata.{_metadata_key(key)}",
                _metadata_value(value),
            )
    except Exception:
        pass
