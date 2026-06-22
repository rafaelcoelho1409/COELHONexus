"""YCS observability — node-level OTel spans (service.py) + retrieval-tier
db.* / gen_ai.* spans (spans.py)."""
from .metrics import (
    record_ask_run,
    record_graded_docs,
    record_retrieved_docs,
    record_rewrite,
    record_subquestion,
)
from .service import attach_span_attrs, traced
from .spans import (
    es_search_span,
    neo4j_query_span,
    qdrant_search_span,
    reranker_span,
    ycs_retriever_fanout_span,
)


__all__ = [
    "attach_span_attrs",
    "traced",
    "record_ask_run",
    "record_retrieved_docs",
    "record_graded_docs",
    "record_rewrite",
    "record_subquestion",
    "qdrant_search_span",
    "es_search_span",
    "neo4j_query_span",
    "reranker_span",
    "ycs_retriever_fanout_span",
]
