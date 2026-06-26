"""ycs/retriever — multi-source orchestrator with FlashRank rerank.

Strategy (Phase 4):
  1. Fan out Qdrant + Neo4j in parallel via `asyncio.gather`
  2. Merge surviving results (deduped via `domain.dedupe_documents`)
  3. FlashRank cross-encoder reranks
  4. If both arms fail → fall back to ES full-text
  5. If all three fail → return [] (caller's responsibility to rewrite)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable

from langchain_core.documents import Document

from . import domain
from .elasticsearch import ElasticsearchRetriever
from .neo4j import Neo4jRetriever
from .params import SMART_DEFAULT_TOP_K
from .qdrant_hybrid import QdrantHybridRetriever


logger = logging.getLogger(__name__)


class SmartRetriever:
    """Multi-source retrieval with graceful degradation. The agent
    consumes only this; the three underlying retrievers are
    construction-time deps."""

    def __init__(
        self,
        es_retriever: ElasticsearchRetriever,
        qdrant_retriever: QdrantHybridRetriever | None = None,
        neo4j_retriever: Neo4jRetriever | None = None,
        use_reranker: bool = True,
        top_k: int = SMART_DEFAULT_TOP_K,
    ) -> None:
        self.es_retriever = es_retriever
        self.qdrant_retriever = qdrant_retriever
        self.neo4j_retriever = neo4j_retriever
        self.use_reranker = use_reranker
        self.top_k = top_k

    async def retrieve(
        self, query: str, channel_ids: list[str] | None = None,
    ) -> list[Document]:
        from domains.ycs.runtime.observability import ycs_retriever_fanout_span
        with ycs_retriever_fanout_span(top_k = self.top_k):
            return await self._retrieve_inner(query, channel_ids)

    async def _retrieve_inner(
        self, query: str, channel_ids: list[str] | None = None,
    ) -> list[Document]:
        # Fan out the two PRIMARY arms (Qdrant + Neo4j) in parallel.
        # ES is the fallback only — we don't pay its latency unless the
        # primaries return nothing usable.
        tasks: dict[str, Awaitable[list[Document]]] = {}
        if self.qdrant_retriever:
            tasks["qdrant"] = self.qdrant_retriever.retrieve(query, channel_ids)
        if self.neo4j_retriever:
            tasks["neo4j"] = self.neo4j_retriever.retrieve(query, channel_ids)

        if tasks:
            results = await asyncio.gather(
                *tasks.values(), return_exceptions = True,
            )
            all_docs: list[Document] = []
            for name, result in zip(tasks.keys(), results):
                if isinstance(result, Exception):
                    logger.warning(
                        f"[ycs:smart] {name} failed: "
                        f"{type(result).__name__}: {str(result)[:200]}"
                    )
                    continue
                logger.info(
                    f"[ycs:smart] {name} returned {len(result)} documents"
                )
                all_docs.extend(result)
            if all_docs:
                deduped = domain.dedupe_documents(all_docs)
                return self._rerank(query, deduped)

        # Primaries returned nothing — fall back to ES full-text.
        try:
            docs = await self.es_retriever.retrieve(query, channel_ids)
            return self._rerank(query, docs)
        except Exception:
            return []

    def _rerank(
        self, query: str, documents: list[Document],
    ) -> list[Document]:
        """Two-stage retrieval: arms = high recall, rerank = high
        precision. FlashRank sees (query, document) pairs together so
        it catches interactions the bi-encoders miss. CPU-local —
        ~50ms for 20 docs."""
        if not self.use_reranker or len(documents) <= 1:
            return documents[:self.top_k]
        try:
            from domains.ycs.reranker import rerank_documents
            return rerank_documents(query, documents, top_k = self.top_k)
        except Exception as e:
            logger.warning(
                f"[ycs:smart] rerank failed ({type(e).__name__}: {e}); "
                f"falling back to retrieval order"
            )
            return documents[:self.top_k]
