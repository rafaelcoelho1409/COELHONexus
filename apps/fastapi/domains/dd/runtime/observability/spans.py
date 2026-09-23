"""DD shared `db.*` spans — for the handful of call sites where Planner
AND Synth routers both query the LangGraph `checkpoints` table directly
(recent-threads listing, resume lookups) rather than through
`AsyncPostgresSaver` (whose own internals stay unwrapped — third-party
library code, not ours to instrument). Mirrors
`domains.ycs.runtime.observability.spans._db_span`/
`domains.rr.runtime.observability.spans._db_span`.
"""
from __future__ import annotations
import infra

import contextlib
from typing import Iterator

from opentelemetry import trace


@contextlib.contextmanager
def postgres_span(operation: str, **attrs) -> Iterator[object | None]:
    tracer = infra.otel.service.get_tracer()
    if tracer is None:
        yield None
        return
    span_attrs: dict = {"db.system": "postgresql", "db.operation": operation}
    for k, v in attrs.items():
        if v is not None:
            span_attrs[k] = v
    with tracer.start_as_current_span(
        f"db.postgresql.{operation}",
        kind       = trace.SpanKind.CLIENT,
        attributes = span_attrs,
    ) as span:
        try:
            yield span
        except Exception as e:
            span.set_attribute("error.type", type(e).__name__)
            span.record_exception(e)
            raise
