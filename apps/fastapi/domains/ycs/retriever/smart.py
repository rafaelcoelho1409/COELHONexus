"""ycs/retriever — multi-source orchestrator with FlashRank rerank.

Strategy (2026-09-15: 3-way parallel — was Qdrant+Neo4j with ES as
sequential fallback):
  1. Fan out Qdrant + Neo4j + ES full-text in ONE `asyncio.gather`
  2. Merge surviving results (deduped via `domain.dedupe_documents`)
  3. FlashRank cross-encoder reranks
  4. All arms empty/failed → return [] (caller's responsibility to rewrite)

Rationale: ES is millisecond-scale local full-text — running it inline
costs max(arms) latency, not the sum, and keyword recall complements
vector (Qdrant) + graph (Neo4j) on exact-match questions (titles,
names, quoted phrases) where embeddings underperform. The old
sequential fallback saved nothing measurable and starved those
queries of keyword hits whenever a primary returned anything."""
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
        # Fan out ALL THREE arms (Qdrant + Neo4j + ES) in parallel —
        # ES latency hides inside max(arms), and keyword hits complement
        # vector + graph recall on exact-match questions.
        tasks: dict[str, Awaitable[list[Document]]] = {}
        if self.qdrant_retriever:
            tasks["qdrant"] = self.qdrant_retriever.retrieve(query, channel_ids)
        if self.neo4j_retriever:
            tasks["neo4j"] = self.neo4j_retriever.retrieve(query, channel_ids)
        tasks["es"] = self.es_retriever.retrieve(query, channel_ids)

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

        # Every arm empty or failed — caller rewrites and retries.
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
