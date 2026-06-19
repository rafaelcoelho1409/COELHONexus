"""Lazy instrument factory — one OTel instrument per MetricSpec in
metrics_registry.INSTRUMENTS. Idempotent: instruments are created on the
first `get_instrument(key)` call after `init_otel()` has run.

Domain `record_*` functions call `get_instrument(key)` and dispatch on
the returned instrument's `.add()` (counters) or `.record()` (histograms).
A None return means OTel isn't initialized (or the registry failed to
build) — recorders treat it as a no-op.
"""
from __future__ import annotations

import logging

from .metrics_registry import INSTRUMENTS
from .service import get_meter


logger = logging.getLogger(__name__)


_instruments: dict = {}


def _ensure_instruments() -> dict:
    if _instruments:
        return _instruments
    try:
        meter = get_meter()
        for spec in INSTRUMENTS:
            kwargs = {"name": spec.name, "description": spec.description}
            if spec.unit:
                kwargs["unit"] = spec.unit
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
