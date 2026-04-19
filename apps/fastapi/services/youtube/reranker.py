"""
FlashRank Cross-Encoder Reranker

CONCEPT: Retrievers optimize for RECALL (find as many relevant docs as possible).
Rerankers optimize for PRECISION (put the most relevant docs at the top).

The two-stage pipeline:
  1. Retriever casts a wide net → returns 20-50 candidates
  2. Reranker scores each candidate precisely → returns top_k best

Why two stages? Cross-encoders (rerankers) are more accurate than bi-encoders
(embedding models) because they process query + document TOGETHER, capturing
fine-grained interactions. But they're slower — O(n) per query instead of
O(1) with pre-computed embeddings. So we use the fast retriever first,
then the precise reranker on the smaller candidate set.

FlashRank is a lightweight, open-source reranker that runs locally on CPU.
No API cost, no network latency, ~50ms for 20 documents.
"""
from langchain_core.documents import Document

# FlashRank is imported lazily to avoid startup cost if not used
_ranker = None


def _get_ranker():
    """Lazy-load the FlashRank model (downloads on first use, ~100MB)."""
    global _ranker
    if _ranker is None:
        from flashrank import Ranker
        _ranker = Ranker()
    return _ranker


def rerank_documents(
    query: str,
    documents: list[Document],
    top_k: int = 10,
) -> list[Document]:
    """
    Rerank documents using FlashRank cross-encoder.

    CONCEPT: FlashRank takes (query, passage) pairs and scores them.
    Higher score = more relevant. We sort by score and return top_k.

    The reranker sees the FULL query-document interaction, not just
    embedding similarity. This catches cases where a document is
    semantically similar but doesn't actually answer the question.

    Example:
      Query: "How to fine-tune LLMs?"
      Doc A: "Fine-tuning LLMs requires..." (score: 0.95) ← answers it
      Doc B: "LLMs are large language models that..." (score: 0.40) ← related but doesn't answer

    A bi-encoder might rank both similarly (both about LLMs).
    A cross-encoder correctly puts Doc A first.
    """
    if not documents:
        return []
    ranker = _get_ranker()
    # Build FlashRank input format
    from flashrank import RerankRequest
    passages = [
        {"id": i, "text": doc.page_content[:2000]}
        for i, doc in enumerate(documents)
    ]
    rerank_request = RerankRequest(
        query = query,
        passages = passages,
    )
    results = ranker.rerank(rerank_request)
    # Map back to Document objects, sorted by rerank score
    reranked = []
    for result in results[:top_k]:
        idx = result["id"]
        doc = documents[idx]
        # Add rerank score to metadata
        doc.metadata["rerank_score"] = result["score"]
        reranked.append(doc)
    return reranked
