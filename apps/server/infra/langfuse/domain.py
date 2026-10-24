"""langfuse domain — pure span-attribute encoders, the export span gate and
OTLP string transforms (no I/O, deterministic)."""
from __future__ import annotations
from . import keys, params, patterns

import base64
import json
from collections.abc import Mapping
from typing import Any


# --- Export span gate ----------------------------------------------------------

def has_genai_attrs(attributes: Mapping[str, object]) -> bool:
    """True if any attribute key is a `gen_ai.*` semconv name — LiteLLM /
    rotator LLM-call spans carry these."""
    return any(str(key).startswith("gen_ai.") for key in attributes)


def is_mcp_transport_drop(name: str) -> bool:
    """Explicit deny, checked before any allow rule — MCP client transport
    spans + JSON-RPC protocol-level spans (see keys.MCP_TRANSPORT_DROPS)."""
    return any(name.startswith(prefix) for prefix in keys.MCP_TRANSPORT_DROPS)


def is_curated_keep(name: str, attributes: Mapping[str, object]) -> bool:
    """True when domain code opted in explicitly, or the span name matches a
    known domain/task-root prefix.

    No `"rotator."` prefix here (removed 2026-09-22) — that matched spans
    the OLD internal `domains/llm/` gateway used to emit before it was
    retired 2026-09-21 in favor of the external rotator repo +
    `domains/settings/chat`+`embeddings`. Nothing in this app creates a
    `rotator.*`-named span anymore; the external Rotator's own spans (once
    it stops no-op-stubbing them) go through its own exporter pipeline in
    its own process, never through this gate."""
    if attributes.get(keys.KEEP) is True:
        return True
    return (
        name.startswith(("dd.", "rr.", "ycs.", "mcp.tool."))
        or any(name.startswith(prefix) for prefix in keys.CELERY_DOMAIN_PREFIXES)
    )


def should_keep_span(name: str, attributes: Mapping[str, object]) -> bool:
    """Default-deny allow-list for the LangFuse export arm.

    Only spans explicitly in an allow category reach LangFuse. Everything
    else (HTTP instrumentation, Redis chatter, Celery internals, health
    probes, unknown spans) is dropped at the processor and exporter — see
    service.py:LangFuseFilterProcessor / LangFuseFilterExporter.
    """
    if is_mcp_transport_drop(name):
        return False
    return is_curated_keep(name, attributes) or has_genai_attrs(attributes)


# --- OTLP string transforms ----------------------------------------------------

def basic_auth_header(public_key: str, secret_key: str) -> str:
    """HTTP Basic auth value for LangFuse's OTLP endpoint."""
    return base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()


def normalize_traces_endpoint(endpoint: str) -> str:
    """Ensure `endpoint` ends with `/v1/traces` (LangFuse's OTLP traces path)."""
    stripped = endpoint.rstrip("/")
    if stripped.endswith("/v1/traces"):
        return stripped
    return f"{stripped}/v1/traces"


# --- Span-attribute encoders ---------------------------------------------------


def truncate(text: str, cap: int) -> str:
    if cap <= 0 or len(text) <= cap:
        return text
    return text[:cap] + f"…+{len(text) - cap}b"


def json_attr(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        raw = json.dumps(str(value), ensure_ascii=False)
    return truncate(raw, params.JSON_ATTR_CAP)


def metadata_key(key: str) -> str:
    safe = patterns.SAFE_KEY_RE.sub("_", str(key).strip())
    return safe.strip("._-") or "value"


def metadata_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return truncate(value, params.METADATA_VALUE_CAP)
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        raw = str(value)
    return truncate(raw, params.METADATA_VALUE_CAP)
