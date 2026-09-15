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
        extra_queries: list[str] | None = None,
    ) -> list[Document]:
        from domains.ycs.runtime.observability import ycs_retriever_fanout_span
        with ycs_retriever_fanout_span(top_k = self.top_k):
            docs, _ = await self._retrieve_inner(
                query, channel_ids, extra_queries, frozenset(),
            )
            return docs

    async def retrieve_detailed(
        self, query: str, channel_ids: list[str] | None = None,
        extra_queries: list[str] | None = None,
        skip_arms: frozenset[str] | None = None,
    ) -> tuple[list[Document], dict[str, str]]:
        """Like `retrieve` but honors `skip_arms` (per-request breaker
        state owned by the caller) and returns per-arm statuses:
        "ok" (docs found), "empty" (ran clean, nothing), "failed"
        (exception — provider-side), "skipped" (breaker). Neo4j reports
        its own "extraction_failed" distinctly (see its `retrieve`)."""
        from domains.ycs.runtime.observability import ycs_retriever_fanout_span
        with ycs_retriever_fanout_span(top_k = self.top_k):
            return await self._retrieve_inner(
                query, channel_ids, extra_queries, skip_arms or frozenset(),
            )

    @staticmethod
    def _arm_group(task_name: str) -> str:
        """Collapse per-variant task keys (qdrant_alt1, es_alt1) back to
        their arm for breaker bookkeeping."""
        if task_name.startswith("qdrant"):
            return "qdrant"
        if task_name.startswith("es"):
            return "es"
        return task_name

    async def _retrieve_inner(
        self, query: str, channel_ids: list[str] | None = None,
        extra_queries: list[str] | None = None,
        skip_arms: frozenset[str] = frozenset(),
    ) -> tuple[list[Document], dict[str, str]]:
        # Fan out ALL THREE arms (Qdrant + Neo4j + ES) in parallel —
        # ES latency hides inside max(arms), and keyword hits complement
        # vector + graph recall on exact-match questions.
        #
        # 2026-09-15 dual-query (SemEval-2026 winner pattern — multiple
        # cheap rewrites fused, NOT HyDE pseudo-documents which
        # underperform vanilla dense): when the caller passes distinct
        # extra queries (e.g. original question + contextualize's
        # standalone rewrite), the CHEAP arms (Qdrant + ES) run each
        # query variant in the same gather — zero extra serial latency.
        # Neo4j stays single-query on the primary: its retrieval spends
        # an LLM call per query, and doubling that per retrieve is real
        # money/latency for unproven gain on the graph arm.
        queries = [query]
        for extra in extra_queries or []:
            cleaned = (extra or "").strip()
            if cleaned and cleaned.lower() not in {q.lower() for q in queries}:
                queries.append(cleaned)
        tasks: dict[str, Awaitable] = {}
        if self.qdrant_retriever and "qdrant" not in skip_arms:
            for i, q in enumerate(queries):
                key = "qdrant" if i == 0 else f"qdrant_alt{i}"
                tasks[key] = self.qdrant_retriever.retrieve(q, channel_ids)
        if self.neo4j_retriever and "neo4j" not in skip_arms:
            tasks["neo4j"] = self.neo4j_retriever.retrieve(query, channel_ids)
        if "es" not in skip_arms:
            for i, q in enumerate(queries):
                key = "es" if i == 0 else f"es_alt{i}"
                tasks[key] = self.es_retriever.retrieve(q, channel_ids)

        # 2026-09-15 per-request arm breaker: a FAILED arm (provider
        # error, not clean-empty) is skipped on later rounds of the same
        # request; a clean-EMPTY arm is skipped after 2 consecutive
        # empties (a keyword-less query won't grow keywords on rewrite).
        # The caller owns the skip set and never skips Qdrant (primary
        # vector arm stays live).
        arm_docs: dict[str, int] = {}
        arm_status: dict[str, str] = {a: "skipped" for a in skip_arms}
        if tasks:
            results = await asyncio.gather(
                *tasks.values(), return_exceptions = True,
            )
            all_docs: list[Document] = []
            for name, result in zip(tasks.keys(), results):
                group = self._arm_group(name)
                if isinstance(result, Exception):
                    logger.warning(
                        f"[ycs:smart] {name} failed: "
                        f"{type(result).__name__}: {str(result)[:200]}"
                    )
                    arm_status[group] = "failed"
                    continue
                if group == "neo4j" and isinstance(result, tuple):
                    docs, neo_status = result
                    arm_docs[group] = arm_docs.get(group, 0) + len(docs)
                    if neo_status in ("extraction_failed", "traversal_failed"):
                        arm_status[group] = "failed"
                    elif arm_docs[group] > 0:
                        arm_status[group] = "ok"
                    else:
                        arm_status.setdefault(group, "empty")
                    logger.info(
                        f"[ycs:smart] {name} returned {len(docs)} "
                        f"documents ({neo_status})"
                    )
                    all_docs.extend(docs)
                    continue
                logger.info(
                    f"[ycs:smart] {name} returned {len(result)} documents"
                )
                arm_docs[group] = arm_docs.get(group, 0) + len(result)
                if len(result) > 0:
                    arm_status[group] = "ok"
                else:
                    arm_status.setdefault(group, "empty")
                all_docs.extend(result)
            # Qdrant is the load-bearing arm — its status is reported
            # like the others, but the caller (retrieve node) never adds
            # it to `skip_arms`.
            if all_docs:
                deduped = domain.dedupe_documents(all_docs)
                return self._rerank(query, deduped), arm_status

        # Every arm empty or failed — caller rewrites and retries.
        return [], arm_status

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
