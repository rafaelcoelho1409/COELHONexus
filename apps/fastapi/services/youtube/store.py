"""Qdrant store for YouTube transcript chunks.

v0: single collection `youtube-transcripts`, dim=2048 Cosine, dense-only.
Matches the rotator's NIM `llama-nemotron-embed-1b-v2` output dimension
(see services/llm/chain.py). Sparse vectors (BGE-M3) layered in later
per docs/YCS-MIGRATION-SOTA-2026-05-19.md §3 row 6.
"""
import hashlib
import os
import uuid
from functools import lru_cache

from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from services.llm.chain import embed_via_router_async


COLLECTION = "youtube-transcripts"
DENSE_DIM = 2048
DENSE_DISTANCE = Distance.COSINE
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200


@lru_cache(maxsize=1)
def _client() -> AsyncQdrantClient:
    return AsyncQdrantClient(
        url=os.environ["QDRANT_URL"],
        api_key=os.environ["QDRANT_API_KEY"],
        timeout=30,
    )


async def ensure_collection() -> None:
    """Idempotent: create the YCS collection if missing, no-op if present."""
    qc = _client()
    existing = {c.name for c in (await qc.get_collections()).collections}
    if COLLECTION in existing:
        return
    await qc.create_collection(
        collection_name=COLLECTION,
        vectors_config=VectorParams(size=DENSE_DIM, distance=DENSE_DISTANCE),
    )


@lru_cache(maxsize=1)
def _chunker() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
        length_function=len,
    )


def _point_id(video_id: str, chunk_index: int) -> str:
    # Qdrant point IDs must be int or valid UUID. md5 hex is 32 chars —
    # the exact size of a UUID without dashes — so uuid.UUID parses it
    # and gives us a deterministic, idempotent id per (video, chunk).
    digest = hashlib.md5(f"{video_id}_{chunk_index}".encode()).hexdigest()
    return str(uuid.UUID(digest))


async def upsert_chunks(
    video_id: str,
    title: str,
    lang: str | None,
    transcript_text: str,
) -> int:
    """Chunk → embed → upsert. Returns number of points written.

    Deterministic IDs by (video_id, chunk_index): re-running on the same
    video overwrites the same point set in place.
    """
    if not transcript_text or not transcript_text.strip():
        return 0
    chunks = _chunker().split_text(transcript_text)
    vectors = await embed_via_router_async(chunks, input_type="passage")
    total = len(chunks)
    points = [
        PointStruct(
            id=_point_id(video_id, i),
            vector=vectors[i],
            payload={
                "content": chunks[i],
                "video_id": video_id,
                "chunk_index": i,
                "total_chunks": total,
                "title": title,
                "lang": lang or "",
            },
        )
        for i in range(total)
    ]
    await _client().upsert(collection_name=COLLECTION, points=points)
    return total


async def search(question: str, top_k: int = 10) -> list[dict]:
    """Vector search: embed query → Qdrant → top_k chunks (payload + score).

    Each result is the chunk payload (`content`, `video_id`, `title`,
    `chunk_index`, `total_chunks`, `lang`) augmented with `score`.
    """
    if not question or not question.strip():
        return []
    vec = (await embed_via_router_async([question], input_type="query"))[0]
    response = await _client().query_points(
        collection_name=COLLECTION,
        query=vec,
        limit=top_k,
        with_payload=True,
    )
    return [{**p.payload, "score": p.score} for p in response.points]
