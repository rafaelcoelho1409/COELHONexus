"""YCS retriever-side spans — db.* semconv for the storage tier
(Qdrant / Elasticsearch / Neo4j / Postgres) and gen_ai.* for the rerank step.

Wraps the hot-path search calls in OTel spans so each YCS Ask traces
out as: orchestrator → adaptive_node → qdrant_search → es_search →
neo4j_lookup → rerank → gen_ai.completion. Each span carries the
canonical `db.*` semconv attributes plus a small set of YCS-specific
labels (collection / index / channel_filter_count).

Usage:
    from domains.ycs.runtime.observability.spans import qdrant_search_span
    with qdrant_search_span(collection="...", top_k=10, channel_filter_count=1):
        results = await self.qdrant.query_points(...)

The `with` blocks are sync; the actual I/O `await` happens inside. OTel
context propagates via contextvars across await boundaries.
"""
from __future__ import annotations
import infra

import contextlib
from typing import Iterator

from opentelemetry import trace


@contextlib.contextmanager
def _db_span(
    system: str,
    operation: str,
    **attrs,
) -> Iterator[object | None]:
    """Common shell — gen_ai- and db.* spans share this skeleton."""
    tracer = infra.otel.service.get_tracer()
    if tracer is None:
        yield None
        return
    span_attrs: dict = {
        "db.system":    system,
        "db.operation": operation,
    }
    for k, v in attrs.items():
        if v is not None:
            span_attrs[k] = v
    with tracer.start_as_current_span(
        f"db.{system}.{operation}",
        kind        = trace.SpanKind.CLIENT,
        attributes  = span_attrs,
    ) as span:
        try:
            yield span
        except Exception as e:
            span.set_attribute("error.type", type(e).__name__)
            span.record_exception(e)
            raise


@contextlib.contextmanager
def qdrant_search_span(
    *,
    collection:            str,
    top_k:                 int,
    channel_filter_count:  int = 0,
    operation:             str = "query_points",
) -> Iterator[object | None]:
    """Qdrant `query_points` / `search` — RRF-fused hybrid retrieval."""
    with _db_span(
        "qdrant", operation,
        **{
            "db.qdrant.collection_name":  collection,
            "db.qdrant.top_k":            top_k,
            "db.qdrant.channel_filter":   channel_filter_count,
        },
    ) as span:
        yield span


@contextlib.contextmanager
def es_search_span(
    *,
    index:                 str,
    top_k:                 int,
    channel_filter_count:  int = 0,
    operation:             str = "search",
) -> Iterator[object | None]:
    """Elasticsearch `search` — full-text or metadata lookup."""
    with _db_span(
        "elasticsearch", operation,
        **{
            "db.elasticsearch.index":          index,
            "db.elasticsearch.top_k":          top_k,
            "db.elasticsearch.channel_filter": channel_filter_count,
        },
    ) as span:
        yield span


@contextlib.contextmanager
def qdrant_upsert_span(
    *,
    collection:   str,
    point_count:  int,
) -> Iterator[object | None]:
    """Qdrant `upsert` — ingestion write path, distinct from
    `qdrant_search_span`'s read-path shape (`point_count` vs `top_k`)."""
    with _db_span(
        "qdrant", "upsert",
        **{
            "db.qdrant.collection_name": collection,
            "db.qdrant.point_count":     point_count,
        },
    ) as span:
        yield span


@contextlib.contextmanager
def qdrant_admin_span(
    *,
    operation:   str,
    collection:  str | None = None,
) -> Iterator[object | None]:
    """Qdrant collection/alias admin ops (cutover, cleanup) — distinct
    from the read/write hot paths above, which all target one
    collection's points rather than the collection catalog itself."""
    with _db_span(
        "qdrant", operation,
        **{"db.qdrant.collection_name": collection},
    ) as span:
        yield span


@contextlib.contextmanager
def neo4j_query_span(
    *,
    operation:         str,
    statement_summary: str | None = None,
) -> Iterator[object | None]:
    """Neo4j Cypher query — pass a short summary, not the raw statement,
    to keep span sizes small and avoid leaking parameter values."""
    with _db_span(
        "neo4j", operation,
        **{
            "db.statement": statement_summary,
        },
    ) as span:
        yield span


@contextlib.contextmanager
def postgres_span(
    *,
    operation:  str,
    table:      str | None = None,
    row_count:  int | None = None,
) -> Iterator[object | None]:
    """Postgres query — `table` names the primary table touched; `row_count`
    for batch inserts/updates when known upfront. Added 2026-09-22 alongside
    `conversation/service.py`/`query/service.py`'s Postgres calls — until
    then only Qdrant/ES/Neo4j had a wrapper here."""
    with _db_span(
        "postgresql", operation,
        **{
            "db.postgres.table":     table,
            "db.postgres.row_count": row_count,
        },
    ) as span:
        yield span


@contextlib.contextmanager
def ycs_retriever_fanout_span(*, top_k: int) -> Iterator[object | None]:
    """Parent span over the multi-retriever fan-out — qdrant + neo4j + es +
    reranker all become child spans under this one in Tempo / LangFuse."""
    tracer = infra.otel.service.get_tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(
        "ycs.retriever.smart_fanout",
        kind        = trace.SpanKind.INTERNAL,
        attributes  = {"ycs.top_k": top_k},
    ) as span:
        try:
            yield span
        except Exception as e:
            span.set_attribute("error.type", type(e).__name__)
            span.record_exception(e)
            raise


@contextlib.contextmanager
def reranker_span(
    *,
    model:     str,
    doc_count: int,
    top_k:     int,
) -> Iterator[object | None]:
    """Cross-encoder rerank — gen_ai.* semconv since this is an LM workload."""
    tracer = infra.otel.service.get_tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(
        "gen_ai.rerank",
        kind = trace.SpanKind.CLIENT,
        attributes = {
            "gen_ai.system":           "flashrank",
            "gen_ai.operation.name":   "rerank",
            "gen_ai.request.model":    model,
            "gen_ai.rerank.doc_count": doc_count,
            "gen_ai.rerank.top_k":     top_k,
        },
    ) as span:
        try:
            yield span
        except Exception as e:
            span.set_attribute("error.type", type(e).__name__)
            span.record_exception(e)
            raise
