"""Baggage propagation — stamp cross-cutting identifiers (study_id, channel_id,
digest_id, tenant, arm_name, session_id, user_id) into the active OTel context
so every child span auto-inherits them. No kwargs threading through 10 layers.

Pattern:
    with bag_context(study_id="abc", framework="claude-code"):
        # every span inside the block carries `study_id` + `framework` attrs
        await run_pipeline()

`BaggageSpanProcessor` (attached in `init_otel()`) is what mirrors baggage
entries onto span attributes. Without it baggage still propagates through
context but won't appear on spans.

Keys are allow-listed (ALLOWED_BAGGAGE_KEYS) so high-cardinality values
can't accidentally explode Tempo/LangFuse storage.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Iterator

from opentelemetry import baggage as _baggage
from opentelemetry import context as _context


logger = logging.getLogger(__name__)


ALLOWED_BAGGAGE_KEYS: frozenset[str] = frozenset({
    "study_id",
    "framework",
    "channel_id",
    "digest_id",
    "tenant",
    "arm_name",
    "session_id",
    "user_id",
    "pipeline",
    # LangFuse v3 OTel ingester promotes these to top-level session_id /
    # user_id / tags on the trace (separate from plain `session_id` which
    # only lands as a span attribute). Keep both — same value, two names.
    "langfuse.session.id",
    "langfuse.user.id",
    "langfuse.tags",
})


def get_baggage_processor():
    """Return a BaggageSpanProcessor mirroring ALLOWED_BAGGAGE_KEYS onto spans.

    Returns None if the optional `opentelemetry-processor-baggage` package
    isn't installed — init_otel() then proceeds without it (baggage still
    propagates in context, just not onto spans)."""
    try:
        from opentelemetry.processor.baggage import BaggageSpanProcessor
    except Exception as e:
        logger.warning(
            f"[otel] BaggageSpanProcessor unavailable "
            f"({type(e).__name__}: {e}) — baggage will propagate through "
            "context but won't appear as span attributes"
        )
        return None

    def predicate(key: str) -> bool:
        return key in ALLOWED_BAGGAGE_KEYS

    try:
        return BaggageSpanProcessor(predicate)
    except Exception as e:
        logger.warning(f"[otel] BaggageSpanProcessor init failed: {e}")
        return None


@contextlib.contextmanager
def bag_context(**kwargs: str | None) -> Iterator[None]:
    """Attach baggage entries for the duration of the `with` block.

    None values are dropped (lets callers pass optional ids without guards).
    Unknown keys (not in ALLOWED_BAGGAGE_KEYS) still propagate but won't
    be mirrored to spans.
    """
    ctx = _context.get_current()
    for key, value in kwargs.items():
        if value is None:
            continue
        ctx = _baggage.set_baggage(key, str(value), context=ctx)
    token = _context.attach(ctx)
    try:
        yield
    finally:
        _context.detach(token)


def current_baggage() -> dict[str, str]:
    """Snapshot of baggage entries in the active context (diagnostics)."""
    try:
        return dict(_baggage.get_all())
    except Exception:
        return {}
