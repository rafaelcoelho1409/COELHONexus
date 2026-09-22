"""ycs/reranker — FlashRank cross-encoder reranking.

Module-level lazy singleton — `Ranker()` first call downloads ~100 MB
of model weights. Subsequent calls re-use the in-process instance.
"""
from __future__ import annotations
import domains
from . import params

from typing import Any, Optional

from langchain_core.documents import Document


# Lazy — initialized on first `rerank_documents` call.
_ranker: Optional[Any] = None


def _get_ranker():
    """Lazy import + instantiate. Deferring keeps app startup fast for
    paths that don't rerank (e.g. /search, /content/*)."""
    global _ranker
    if _ranker is None:
        from flashrank import Ranker
        _ranker = Ranker()
    return _ranker


def rerank_documents(
    query: str,
    documents: list[Document],
    top_k: int = params.DEFAULT_TOP_K,
) -> list[Document]:
    """Cross-encoder rerank. `rerank_score` is stamped onto each
    returned doc's metadata.

    See deprecated module docstring for the bi-encoder vs cross-encoder
    trade-off (FlashRank sees the full query/doc interaction; embeddings
    only see precomputed vectors)."""
    if not documents:
        return []
    from flashrank import RerankRequest

    with domains.ycs.runtime.observability.spans.reranker_span(
        model     = "flashrank-default",
        doc_count = len(documents),
        top_k     = top_k,
    ):
        ranker = _get_ranker()
        passages = [
            {"id": i, "text": doc.page_content[:params.PER_DOC_CHAR_CAP]}
            for i, doc in enumerate(documents)
        ]
        results = ranker.rerank(RerankRequest(query = query, passages = passages))

        reranked: list[Document] = []
        for result in results[:top_k]:
            idx = result["id"]
            doc = documents[idx]
            doc.metadata["rerank_score"] = result["score"]
            reranked.append(doc)
        return reranked
