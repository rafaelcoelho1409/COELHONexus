"""ycs/retriever — Qdrant dense + sparse RRF hybrid retriever (Phase 2).

Imperative Shell: ONE Qdrant call with `Prefetch` per vector type +
`FusionQuery(fusion=Fusion.RRF)`. Qdrant internally fuses dense +
sparse scores using Reciprocal Rank Fusion — no manual RRF code on
our side.

Direct port of deprecated `services/youtube/retriever.py:L115-230`."""
from __future__ import annotations

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
    SparseVector,
)

from domains.ycs.ingestion import QDRANT_COLLECTION

from .params import QDRANT_DEFAULT_TOP_K


class QdrantHybridRetriever:
    """Dense (NIM `nvidia/llama-nemotron-embed-1b-v2`) + Sparse
    (`FastEmbedSparse("Qdrant/bm25")`) fused in one query. Replaces
    ES full-text on the hot path — dense catches semantic matches,
    sparse catches keyword matches, RRF blends the two ranked lists."""

    def __init__(
        self,
        qdrant: AsyncQdrantClient,
        dense_embeddings: Embeddings,
        sparse_embeddings: FastEmbedSparse,
        top_k: int = QDRANT_DEFAULT_TOP_K,
    ) -> None:
        self.qdrant = qdrant
        self.dense_embeddings = dense_embeddings
        self.sparse_embeddings = sparse_embeddings
        self.top_k = top_k

    async def retrieve(
        self, query: str, channel_ids: list[str] | None = None,
    ) -> list[Document]:
        # Two query-side vectors, one Qdrant call. Each Prefetch over-
        # fetches at `top_k * 2` so RRF has headroom to reorder before
        # the final `limit = top_k` truncation.
        dense_vector = self.dense_embeddings.embed_query(query)
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

        results = await self.qdrant.query_points(
            collection_name = QDRANT_COLLECTION,
            prefetch = prefetch,
            query = FusionQuery(fusion = Fusion.RRF),
            limit = self.top_k,
            with_payload = True,
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
        return documents
