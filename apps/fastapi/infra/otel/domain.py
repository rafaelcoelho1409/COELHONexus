"""otel domain — pure functions (no I/O, deterministic).

The Functional Core for this package: the LangFuse span-gate predicates,
the baggage-key predicate, and small string/dict transforms pulled out of
service.py's I/O functions. Everything here takes plain data in and returns
plain data out — no OTel SDK objects, no env reads, no clocks — so it's
unit-testable without mocks.
"""
from __future__ import annotations
from . import keys

import base64
import logging
from collections.abc import Mapping

# --- LangFuse span gate ------------------------------------------------------

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
    retired 2026-09-21 in favor of the external COELHOLLMRotator repo +
    `domains/settings/chat`+`embeddings`. Nothing in this app creates a
    `rotator.*`-named span anymore; the external Rotator's own spans (once
    it stops no-op-stubbing them) go through its own exporter pipeline in
    its own process, never through this gate."""
    if attributes.get("coelho.langfuse.keep") is True:
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


# --- Baggage ------------------------------------------------------------------

def is_allowed_baggage_key(key: str) -> bool:
    """Allow-list gate for BaggageSpanProcessor (see keys.ALLOWED_BAGGAGE_KEYS)."""
    return key in keys.ALLOWED_BAGGAGE_KEYS


# --- Resource / exporter string transforms ------------------------------------

def parse_resource_attributes(raw: str) -> dict[str, str]:
    """Parse an OTEL_RESOURCE_ATTRIBUTES-style `k=v,k2=v2` string into a dict.
    Pairs missing `=` are skipped."""
    attrs: dict[str, str] = {}
    for pair in raw.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            attrs[k.strip()] = v.strip()
    return attrs


def is_insecure_endpoint(endpoint: str) -> bool:
    """OTLP exporters take `insecure=True` for plaintext `http://` endpoints."""
    return endpoint.startswith("http://")


def basic_auth_header(public_key: str, secret_key: str) -> str:
    """HTTP Basic auth value for LangFuse's OTLP endpoint."""
    return base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()


def normalize_traces_endpoint(endpoint: str) -> str:
    """Ensure `endpoint` ends with `/v1/traces` (LangFuse's OTLP traces path)."""
    stripped = endpoint.rstrip("/")
    if stripped.endswith("/v1/traces"):
        return stripped
    return f"{stripped}/v1/traces"


# --- Metrics --------------------------------------------------------------

def instrument_kwargs(name: str, description: str, unit: str = "") -> dict:
    """Build the create_counter/create_histogram kwargs for one MetricSpec."""
    kwargs: dict = {"name": name, "description": description}
    if unit:
        kwargs["unit"] = unit
    return kwargs


def active_exporter_names(*, alloy_ok: bool, langfuse_ok: bool) -> list[str]:
    """Names of the exporters that attached successfully, for the init log line."""
    names: list[str] = []
    if alloy_ok:
        names.append("alloy")
    if langfuse_ok:
        names.append("langfuse")
    return names


# --- Logging --------------------------------------------------------------

def dedupe_key(record: logging.LogRecord) -> tuple:
    """Cache key for the export-failure log dampener — collapses messages
    that only differ in trailing 'retrying in N.NNs' text."""
    return (record.name, record.levelno, str(record.msg)[:80])
