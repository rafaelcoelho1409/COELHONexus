"""YCS observability — the `@traced` node decorator for RAG nodes (mirrors DD planner/synth), the
retrieval-tier spans with YCS's attribute vocabulary, and the Ask / ingest metric recorders.

Each Ask traces out as: orchestrator → adaptive_node → qdrant_search → es_search → neo4j_lookup →
rerank → gen_ai.completion. The `db.*` client spans are built on the infra modules
(`infra.qdrant.service.span`, `infra.elasticsearch.service.span`, `infra.neo4j.service.span`,
`infra.postgres.service.span`); only the YCS-specific labels (collection / index /
channel_filter_count / ...) are defined here.

Usage:
    with domains.ycs.observability.service.qdrant_search_span(collection = "...", top_k = 10):
        results = await self.qdrant.query_points(...)

The `with` blocks are sync; the actual I/O `await` happens inside. OTel context propagates via
contextvars across await boundaries.
"""
from __future__ import annotations
import infra


traced = infra.opentelemetry.service.node_tracer(
    prefix      = "ycs",
    workflow    = "ycs_ask",
    stage       = "ycs",
    span_prefix = "ycs.node",
    state_attrs = lambda state: {
        "thread_id": state.get("thread_id") or "",
        "question":  (state.get("question") or "")[:200],
    },
)


# ---------------------------------------------------------------------------
# Retrieval-tier spans
# ---------------------------------------------------------------------------

def qdrant_search_span(
    *,
    collection:           str,
    top_k:                int,
    channel_filter_count: int = 0,
    operation:            str = "query_points",
):
    """Qdrant `query_points` / `search` — RRF-fused hybrid retrieval."""
    return infra.qdrant.service.span(
        operation,
        **{
            "db.qdrant.collection_name": collection,
            "db.qdrant.top_k":           top_k,
            "db.qdrant.channel_filter":  channel_filter_count,
        },
    )


def es_search_span(
    *,
    index:                str,
    top_k:                int,
    channel_filter_count: int = 0,
    operation:            str = "search",
):
    """Elasticsearch `search` — full-text or metadata lookup."""
    return infra.elasticsearch.service.span(
        operation,
        **{
            "db.elasticsearch.index":          index,
            "db.elasticsearch.top_k":          top_k,
            "db.elasticsearch.channel_filter": channel_filter_count,
        },
    )


def qdrant_upsert_span(*, collection: str, point_count: int):
    """Qdrant `upsert` — ingestion write path, distinct from `qdrant_search_span`'s
    read-path shape (`point_count` vs `top_k`)."""
    return infra.qdrant.service.span(
        "upsert",
        **{
            "db.qdrant.collection_name": collection,
            "db.qdrant.point_count":     point_count,
        },
    )


def qdrant_admin_span(*, operation: str, collection: str | None = None):
    """Qdrant collection/alias admin ops (cutover, cleanup) — distinct from the read/write
    hot paths above, which all target one collection's points rather than the catalog."""
    return infra.qdrant.service.span(
        operation, **{"db.qdrant.collection_name": collection},
    )


def neo4j_query_span(*, operation: str, statement_summary: str | None = None):
    """Neo4j Cypher query — pass a short summary, not the raw statement, to keep span sizes
    small and avoid leaking parameter values."""
    return infra.neo4j.service.span(operation, **{"db.statement": statement_summary})


def postgres_span(*, operation: str, table: str | None = None, row_count: int | None = None):
    """Postgres query — `table` names the primary table touched; `row_count` for batch
    inserts/updates when known upfront."""
    return infra.postgres.service.span(
        operation,
        **{
            "db.postgres.table":     table,
            "db.postgres.row_count": row_count,
        },
    )


def ycs_retriever_fanout_span(*, top_k: int):
    """Parent span over the multi-retriever fan-out — qdrant + neo4j + es + reranker all
    become child spans under this one in Tempo / LangFuse."""
    return infra.opentelemetry.service.client_span(
        "ycs.retriever.smart_fanout",
        {"ycs.top_k": top_k},
        kind = infra.opentelemetry.service.SpanKind.INTERNAL,
    )


def reranker_span(*, model: str, doc_count: int, top_k: int):
    """Cross-encoder rerank — gen_ai.* semconv since this is an LM workload."""
    return infra.opentelemetry.service.client_span(
        "gen_ai.rerank",
        {
            "gen_ai.system":           "flashrank",
            "gen_ai.operation.name":   "rerank",
            "gen_ai.request.model":    model,
            "gen_ai.rerank.doc_count": doc_count,
            "gen_ai.rerank.top_k":     top_k,
        },
    )


# ---------------------------------------------------------------------------
# Metrics — Ask + extract/ingest pipeline recorders
# ---------------------------------------------------------------------------

def record_ycs_ingest_run(
    *,
    kind: str,
    outcome: str,
    duration_s: float,
) -> None:
    """`kind` ∈ {extract_videos, extract_channel, extract_playlist,
    embedding_migration}; `outcome` ∈ {ok, error}."""
    try:
        attrs = {"kind": kind, "outcome": outcome}
        if (inst := infra.opentelemetry.service.get_instrument("ycs_ingest_run_total")) is not None:
            inst.add(1, attributes = attrs)
        if (inst := infra.opentelemetry.service.get_instrument("ycs_ingest_run_duration")) is not None:
            inst.record(duration_s, attributes = attrs)
    except Exception:
        pass


def record_ask_run(
    *,
    route: str,
    mode: str,
    outcome: str,
    grounded: bool | None = None,
    duration_s: float | None = None,
    citation_count: int | None = None,
) -> None:
    attrs = {
        "route":   route or "unknown",
        "mode":    mode or "unknown",
        "outcome": outcome or "unknown",
    }
    try:
        if (inst := infra.opentelemetry.service.get_instrument("ycs_ask_run_total")) is not None:
            inst.add(1, attributes = attrs)
        if duration_s is not None and (
            inst := infra.opentelemetry.service.get_instrument("ycs_ask_run_duration")
        ) is not None:
            inst.record(max(duration_s, 0.0), attributes = attrs)
        if citation_count is not None and (
            inst := infra.opentelemetry.service.get_instrument("ycs_citation_count")
        ) is not None:
            inst.record(max(citation_count, 0), attributes = attrs)
        if grounded and (inst := infra.opentelemetry.service.get_instrument("ycs_grounded_total")) is not None:
            inst.add(1, attributes = attrs)
    except Exception:
        pass


def record_retrieved_docs(*, route: str, mode: str, count: int) -> None:
    try:
        if (inst := infra.opentelemetry.service.get_instrument("ycs_retrieved_docs")) is not None:
            inst.record(
                max(count, 0),
                attributes = {"route": route or "unknown", "mode": mode or "unknown"},
            )
    except Exception:
        pass


def record_graded_docs(*, route: str, mode: str, count: int) -> None:
    try:
        if (inst := infra.opentelemetry.service.get_instrument("ycs_graded_docs")) is not None:
            inst.record(
                max(count, 0),
                attributes = {"route": route or "unknown", "mode": mode or "unknown"},
            )
    except Exception:
        pass


def record_rewrite(*, route: str, mode: str) -> None:
    try:
        if (inst := infra.opentelemetry.service.get_instrument("ycs_rewrite_total")) is not None:
            inst.add(
                1,
                attributes = {"route": route or "unknown", "mode": mode or "unknown"},
            )
    except Exception:
        pass


def record_subquestion(*, route: str, outcome: str) -> None:
    try:
        if (inst := infra.opentelemetry.service.get_instrument("ycs_subquestion_total")) is not None:
            inst.add(
                1,
                attributes = {
                    "route":   route or "unknown",
                    "outcome": outcome or "unknown",
                },
            )
    except Exception:
        pass
