"""RR store-layer spans — `db.*` semconv for Neo4j/Postgres/Qdrant
(mirrors `domains.ycs.runtime.observability.spans._db_span`), `aws.s3.*`
for MinIO (mirrors `domains.dd.ingestion.storage.service._s3_span` — RR
has its own independent aioboto3 client, not DD's `MinIOStorage` class,
so this isn't shared code, just the same attribute convention).

`domains/rr/stores/service.py` calls these directly at each of its ~25
operations rather than getting one named wrapper per operation (unlike
YCS's `qdrant_search_span`/`es_search_span`) — RR touches 4 backends with
far more distinct operations each (bootstrap/upsert/query/count/...) than
YCS's "search" — one wrapper per exact operation would be ~25 near-
identical functions for no real gain over passing the operation name
straight through.
"""
from __future__ import annotations
import infra

import contextlib
import uuid
from collections.abc import Iterator

from opentelemetry import trace


@contextlib.contextmanager
def _db_span(system: str, operation: str, **attrs) -> Iterator[object | None]:
    tracer = infra.otel.service.get_tracer()
    if tracer is None:
        yield None
        return
    span_attrs: dict = {"db.system": system, "db.operation": operation}
    for k, v in attrs.items():
        if v is not None:
            span_attrs[k] = v
    with tracer.start_as_current_span(
        f"db.{system}.{operation}",
        kind       = trace.SpanKind.CLIENT,
        attributes = span_attrs,
    ) as span:
        try:
            yield span
        except Exception as e:
            span.set_attribute("error.type", type(e).__name__)
            span.record_exception(e)
            raise


def neo4j_span(operation: str, **attrs):
    return _db_span("neo4j", operation, **attrs)


def postgres_span(operation: str, **attrs):
    """`db.statement` (if passed) should be a short summary, not raw SQL
    with parameter values — same rule YCS's `neo4j_query_span` follows."""
    return _db_span("postgresql", operation, **attrs)


def qdrant_span(operation: str, **attrs):
    return _db_span("qdrant", operation, **attrs)


@contextlib.contextmanager
def scan_read_span(operation: str, *, scan_id: str) -> Iterator[None]:
    """Workflow-root span + LangFuse session for the scan READ/viewing
    endpoints (`GET /scan/{id}`, `/llm-counters`, `/fs/*`, `/events`
    SSE, `/scans/recent`, `/finding/{arxiv_id}/code`) — 2026-09-24: this
    whole read side had zero instrumentation (confirmed empty across
    Tempo/LangFuse/Mimir despite ~200 requests per scan from the
    frontend's poll loop), unlike the scan CREATE/execute path which
    already had it. One-shot session id per call — these are stateless
    polls, not a conversation, so there's no natural persistent id to
    reuse the way Ask has a thread_id.

    Supports both `with scan_read_span(...):` for a bounded call AND
    manual `.__enter__()`/`.__exit__()` for a span that must stay open
    across an SSE generator's full lifetime (same reason
    `ycs.query.ai.generate` uses manual enter/exit — a `with` block
    doesn't safely span a generator across client disconnects)."""
    session_id = f"rr-scan-read-{scan_id}-{uuid.uuid4().hex[:8]}"
    with infra.langfuse.service.session(
        "rr-scan-read", session_id = session_id, user_id = "default",
    ), infra.otel.service.get_tracer().start_as_current_span(
        f"rr.scan.{operation}",
        attributes = {
            "coelho.langfuse.keep":                   True,
            "coelho.langfuse.kind":                   "workflow_root",
            "langfuse.trace.name":                    f"rr.scan.{operation}",
            "rr.scan_id":                              scan_id,
            "langfuse.observation.metadata.workflow": "rr_scan_read",
        },
    ):
        yield


@contextlib.contextmanager
def minio_span(
    operation: str, *, bucket: str, key: str | None = None,
) -> Iterator[object | None]:
    """`aws.s3.*` semconv — kept separate from `_db_span` (not a thin
    wrapper over it) since S3-shaped storage uses its own attribute
    namespace, not `db.*`."""
    tracer = infra.otel.service.get_tracer()
    if tracer is None:
        yield None
        return
    attrs: dict = {"aws.s3.bucket": bucket}
    if key is not None:
        attrs["aws.s3.key"] = key
    with tracer.start_as_current_span(
        f"aws.s3.{operation}",
        kind       = trace.SpanKind.CLIENT,
        attributes = attrs,
    ) as span:
        try:
            yield span
        except Exception as e:
            span.set_attribute("error.type", type(e).__name__)
            span.record_exception(e)
            raise
