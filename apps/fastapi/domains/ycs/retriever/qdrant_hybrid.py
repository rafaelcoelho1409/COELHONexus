"""ycs/retriever — Qdrant dense + sparse RRF hybrid retriever (Phase 2).

Imperative Shell: ONE Qdrant call with `Prefetch` per vector type +
`FusionQuery(fusion=Fusion.RRF)`. Qdrant internally fuses dense +
sparse scores using Reciprocal Rank Fusion — no manual RRF code on
our side.
"""
from __future__ import annotations

from elasticsearch import AsyncElasticsearch
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_qdrant import FastEmbedSparse
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    Prefetch,
    SearchParams,
    SparseVector,
)

from domains.ycs.ingestion import QDRANT_COLLECTION
from domains.ycs.runtime.observability import qdrant_search_span

from .params import QDRANT_DEFAULT_TOP_K


class QdrantHybridRetriever:
    """Dense (the Settings-page-configured embedding endpoint — see
    `domains/llm/embeddings`) + Sparse (`FastEmbedSparse("Qdrant/bm25")`)
    fused in one query. Replaces ES full-text on the hot path — dense
    catches semantic matches, sparse catches keyword matches, RRF blends
    the two ranked lists."""

    def __init__(
        self,
        qdrant: AsyncQdrantClient,
        dense_embeddings: Embeddings,
        sparse_embeddings: FastEmbedSparse,
        top_k: int = QDRANT_DEFAULT_TOP_K,
        es_client: AsyncElasticsearch | None = None,
    ) -> None:
        self.qdrant = qdrant
        self.dense_embeddings = dense_embeddings
        self.sparse_embeddings = sparse_embeddings
        self.top_k = top_k
        # 2026-09-16: optional — only used to backfill title/channel on
        # points whose PAYLOAD has them stored empty (see `retrieve()`'s
        # post-fetch patch below). `None` (e.g. tests) just skips that
        # step; nothing else here depends on it.
        self.es_client = es_client

    async def retrieve(
        self, query: str, channel_ids: list[str] | None = None,
    ) -> list[Document]:
        # Two query-side vectors, one Qdrant call. Each Prefetch over-
        # fetches at `top_k * 2` so RRF has headroom to reorder before
        # the final `limit = top_k` truncation.
        dense_vector = await self.dense_embeddings.aembed_query(query)
        sparse_vector = self.sparse_embeddings.embed_query(query)

        # PRE-filter (not post-filter) — applied during the vector
        # search itself per Qdrant best practice.
        query_filter: Filter | None = None
        if channel_ids:
            query_filter = Filter(
                must = [
                    FieldCondition(
                        key = "channel_id",
                        match = MatchAny(any = channel_ids),
                    ),
                ],
            )

        prefetch: list[Prefetch] = [
            Prefetch(
                query =  dense_vector,
                using =  "dense",
                limit =  self.top_k * 2,
                filter = query_filter,
            ),
        ]
        if sparse_vector is not None:
            prefetch.append(
                Prefetch(
                    query = SparseVector(
                        indices = sparse_vector.indices,
                        values =  sparse_vector.values,
                    ),
                    using =  "sparse",
                    limit =  self.top_k * 2,
                    filter = query_filter,
                ),
            )

        with qdrant_search_span(
            collection           = QDRANT_COLLECTION,
            top_k                = self.top_k,
            channel_filter_count = len(channel_ids) if channel_ids else 0,
        ):
            results = await self.qdrant.query_points(
                collection_name = QDRANT_COLLECTION,
                prefetch = prefetch,
                query = FusionQuery(fusion = Fusion.RRF),
                limit = self.top_k,
                with_payload = True,
                # 2026-09-15 (SOTA follow-up): explicit search-time
                # exploration. Server default `ef == ef_construct`
                # couples search to build quality; 128 is the
                # benchmarked probe value (recall climbs, latency
                # flat at our scale). No-op today: 72 points sit
                # under full_scan_threshold, so this is exact scan —
                # it takes effect automatically as the corpus grows
                # past 10k points toward HNSW. (1.16.1 names this
                # `search_params`; per-prefetch `params` left default.)
                search_params = SearchParams(hnsw_ef = 128),
            )

        documents: list[Document] = []
        for point in results.points:
            payload = point.payload or {}
            documents.append(Document(
                page_content = payload.get("content", ""),
                metadata = {
                    "video_id":     payload.get("video_id", ""),
                    "chunk_index":  payload.get("chunk_index", 0),
                    "title":        payload.get("title", ""),
                    "channel":      payload.get("channel", ""),
                    "channel_id":   payload.get("channel_id", ""),
                    "upload_date":  payload.get("upload_date", ""),
                    "webpage_url":  payload.get("webpage_url", ""),
                    "lang":         payload.get("lang", "en"),
                    "score":        point.score,
                    "source":       "qdrant_hybrid",
                },
            ))
        await self._backfill_missing_metadata(documents)
        return documents

    async def _backfill_missing_metadata(
        self, documents: list[Document],
    ) -> None:
        """2026-09-16 fix: patches `title`/`channel` in place for any
        document whose Qdrant PAYLOAD has them stored empty — live-
        verified on the running cluster: split-video chunk points
        (`video_id="XYZ#p3"`) carry `title=""`/`channel=""` baked in
        from an ingestion run that predates `fetch_metadata_from_es`'s
        partition→parent resolution (their content hasn't changed
        since, so the content-hash-skip in `ingest_to_qdrant` means a
        normal re-ingest never re-embeds/re-upserts them — the stale
        payload persists indefinitely). Re-deriving at RETRIEVAL time
        instead of only fixing future writes means every ALREADY-
        INGESTED video gets correct citations immediately, no backfill
        migration needed, and it's a no-op the instant a point does
        carry real values.

        No-op when `es_client` wasn't wired in, or nothing's missing —
        the common case costs one dict comprehension over an already-
        small `documents` list."""
        if self.es_client is None:
            return
        missing_ids = [
            doc.metadata["video_id"]
            for doc in documents
            if doc.metadata.get("video_id")
            and not doc.metadata.get("title")
            and not doc.metadata.get("channel")
        ]
        if not missing_ids:
            return
        from domains.ycs.ingestion.service import fetch_metadata_from_es
        fetched = await fetch_metadata_from_es(self.es_client, missing_ids)
        for doc in documents:
            vid = doc.metadata.get("video_id")
            meta = fetched.get(vid)
            if not meta:
                continue
            doc.metadata["title"] = meta.get("title") or doc.metadata["title"]
            doc.metadata["channel"] = meta.get("channel") or doc.metadata["channel"]
            doc.metadata["upload_date"] = (
                meta.get("upload_date") or doc.metadata["upload_date"]
            )
            doc.metadata["webpage_url"] = (
                meta.get("webpage_url") or doc.metadata["webpage_url"]
            )
