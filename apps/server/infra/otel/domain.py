"""otel domain — pure functions (no I/O, deterministic).

The Functional Core for this package: the baggage-key predicate and small
string/dict transforms pulled out of service.py's I/O functions. (The
LangFuse span gate lives in `infra.langfuse.domain`.) Everything here takes
plain data in and returns plain data out — no OTel SDK objects, no env
reads, no clocks — so it's unit-testable without mocks.
"""
from __future__ import annotations
import infra
from . import keys

import logging

# --- Baggage ------------------------------------------------------------------

def is_allowed_baggage_key(key: str) -> bool:
    """Allow-list gate for BaggageSpanProcessor (see keys.ALLOWED_BAGGAGE_KEYS
    and `infra.langfuse.keys.BAGGAGE_KEYS` for the session / user / tags keys
    LangFuse promotes)."""
    return key in keys.ALLOWED_BAGGAGE_KEYS or key in infra.langfuse.keys.BAGGAGE_KEYS


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
