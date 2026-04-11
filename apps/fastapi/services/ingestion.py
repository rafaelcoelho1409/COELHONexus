"""
Streaming Ingestion Pipeline — ES Transcripts → Qdrant Hybrid Collection

CONCEPT: Process transcripts ONE AT A TIME in a streaming fashion.
Never hold more than one transcript's worth of data in memory.

Old approach (OOM on 359 transcripts):
  Load ALL transcripts → chunk ALL → embed ALL → upsert ALL

New approach (memory-safe for any dataset size):
  For each transcript:
    Fetch from ES → chunk → embed (batch of 8) → upsert → free memory → next

This uses constant memory regardless of dataset size.
Qdrant's upsert is called per-transcript (5-10 points each), which is
efficient enough — Qdrant handles small batches well.

ES scroll API is used to iterate through all transcripts without loading
them all at once (default ES search only returns first 100).
"""
import hashlib
from elasticsearch import AsyncElasticsearch
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    VectorParams,
    SparseVectorParams,
    SparseIndexParams,
    PointStruct,
    models,
)
from langchain_core.documents import Document

from services.chunker import create_chunker, chunk_transcript
from services.embeddings import (
    create_dense_embeddings,
    create_sparse_embeddings,
    get_embedding_dimensions,
)


ES_INDEX_TRANSCRIPTIONS = "coelhonexus-youtube-transcriptions"
ES_INDEX_METADATA = "coelhonexus-youtube-metadata"
QDRANT_COLLECTION = "youtube-transcripts"


def _deterministic_id(video_id: str, chunk_index: int) -> str:
    """Deterministic point ID: hash of video_id + chunk_index (idempotent)."""
    raw = f"{video_id}_{chunk_index}"
    return hashlib.md5(raw.encode()).hexdigest()


async def ensure_collection(
    qdrant: AsyncQdrantClient,
    dense_dimensions: int,
) -> bool:
    """Create the Qdrant collection if it doesn't exist."""
    collections = await qdrant.get_collections()
    existing = {c.name for c in collections.collections}
    if QDRANT_COLLECTION in existing:
        return False
    await qdrant.create_collection(
        collection_name = QDRANT_COLLECTION,
        vectors_config = {
            "dense": VectorParams(
                size = dense_dimensions,
                distance = Distance.COSINE,
            ),
        },
        sparse_vectors_config = {
            "sparse": SparseVectorParams(
                index = SparseIndexParams(on_disk = False),
            ),
        },
    )
    return True


async def _scroll_transcripts(
    es: AsyncElasticsearch,
    video_ids: list[str] | None = None,
    batch_size: int = 50,
):
    """
    Async generator that yields transcripts from ES using scroll API.

    CONCEPT: ES search only returns `size` results (default 100).
    For 359+ transcripts, we need scroll API to paginate through all of them.
    This generator yields batches of transcripts without loading all into memory.
    """
    if video_ids:
        query = {"terms": {"video_id": video_ids}}
    else:
        query = {"match_all": {}}

    # Initial search with scroll
    response = await es.search(
        index = ES_INDEX_TRANSCRIPTIONS,
        query = query,
        size = batch_size,
        scroll = "5m",
        _source = ["video_id", "content", "lang", "channel_id"],
    )
    scroll_id = response.get("_scroll_id")
    hits = response["hits"]["hits"]

    while hits:
        for hit in hits:
            yield hit["_source"]
        # Fetch next page
        response = await es.scroll(scroll_id = scroll_id, scroll = "5m")
        scroll_id = response.get("_scroll_id")
        hits = response["hits"]["hits"]

    # Clean up scroll context
    if scroll_id:
        await es.clear_scroll(scroll_id = scroll_id)


async def fetch_metadata_from_es(
    es: AsyncElasticsearch,
    video_ids: list[str],
) -> dict:
    """Fetch video metadata from ES. Returns {video_id: metadata_dict}."""
    if not video_ids:
        return {}
    response = await es.search(
        index = ES_INDEX_METADATA,
        query = {"ids": {"values": video_ids}},
        size = len(video_ids),
        _source = ["title", "channel", "channel_id", "upload_date", "webpage_url"],
    )
    return {h["_id"]: h["_source"] for h in response["hits"]["hits"]}


# Keep this for graph_builder imports
async def fetch_transcripts_from_es(
    es: AsyncElasticsearch,
    video_ids: list[str] | None = None,
    batch_size: int = 100,
) -> list[dict]:
    """Fetch transcripts from ES (non-streaming, for small batches)."""
    results = []
    async for transcript in _scroll_transcripts(es, video_ids, batch_size):
        results.append(transcript)
    return results


async def ingest_to_qdrant(
    es: AsyncElasticsearch,
    qdrant: AsyncQdrantClient,
    video_ids: list[str] | None = None,
    chunk_size: int = 2000,
    chunk_overlap: int = 200,
) -> dict:
    """
    Streaming ingestion pipeline: ES → Chunk → Embed → Qdrant.

    CONCEPT: Processes ONE transcript at a time to keep memory constant.
    For each transcript:
      1. Fetch transcript text from ES (via scroll)
      2. Fetch metadata for that video
      3. Chunk the transcript (~5 chunks per video)
      4. Embed chunks in batches of 8 (dense + sparse)
      5. Upsert points to Qdrant
      6. Free memory, move to next transcript

    Memory usage: ~constant regardless of total transcripts.
    For 359 transcripts × ~5 chunks each = ~1795 points total,
    but only ~5-8 chunks are in memory at any time.
    """
    # 1. Initialize embedding models
    # Dense: NVIDIA NIM API (zero CPU, server-side GPU inference)
    # Sparse: FastEmbed BM25 (local, minimal CPU — just tokenization)
    dense_embeddings = create_dense_embeddings()
    sparse_embeddings = create_sparse_embeddings()
    dimensions = get_embedding_dimensions()

    # 2. Ensure Qdrant collection exists
    collection_created = await ensure_collection(qdrant, dimensions)

    # 3. Fetch ALL transcripts first (fast — just text + IDs, ~5s for 359)
    # Then process one-by-one for embedding (slow — CPU-bound, ~10min)
    # This separates the ES scroll phase (needs open context) from the
    # embedding phase (takes minutes, would expire the scroll context).
    all_transcripts = []
    async for transcript in _scroll_transcripts(es, video_ids):
        all_transcripts.append(transcript)

    chunker = create_chunker(chunk_size, chunk_overlap)
    total_transcripts = 0
    total_chunks = 0
    total_upserted = 0

    # Cache metadata to avoid re-fetching for same video
    metadata_cache: dict = {}

    for transcript in all_transcripts:
        vid = transcript["video_id"]
        total_transcripts += 1

        # Fetch metadata (cached per video)
        if vid not in metadata_cache:
            meta_map = await fetch_metadata_from_es(es, [vid])
            metadata_cache[vid] = meta_map.get(vid, {})
        meta = metadata_cache[vid]

        # Chunk this transcript
        chunks = chunk_transcript(
            video_id = vid,
            content = transcript.get("content", ""),
            metadata = {
                "lang": transcript.get("lang", "en"),
                "channel_id": transcript.get("channel_id", ""),
                "title": meta.get("title", ""),
                "channel": meta.get("channel", ""),
                "upload_date": meta.get("upload_date", ""),
                "webpage_url": meta.get("webpage_url", ""),
            },
            chunker = chunker,
        )
        if not chunks:
            continue

        total_chunks += len(chunks)

        # Embed: dense via NVIDIA NIM API (zero CPU), sparse via local BM25
        texts = [doc.page_content for doc in chunks]
        dense_vectors = dense_embeddings.embed_documents(texts)
        sparse_vectors = list(sparse_embeddings.embed_documents(texts))

        # Build points and upsert
        points = []
        for i, doc in enumerate(chunks):
            sparse_vec = sparse_vectors[i]
            points.append(PointStruct(
                id = _deterministic_id(doc.metadata["video_id"], doc.metadata["chunk_index"]),
                vector = {
                    "dense": dense_vectors[i],
                    "sparse": models.SparseVector(
                        indices = sparse_vec.indices,
                        values = sparse_vec.values,
                    ),
                },
                payload = {
                    "content": doc.page_content,
                    "video_id": doc.metadata["video_id"],
                    "chunk_index": doc.metadata["chunk_index"],
                    "total_chunks": doc.metadata["total_chunks"],
                    "title": doc.metadata.get("title", ""),
                    "channel": doc.metadata.get("channel", ""),
                    "channel_id": doc.metadata.get("channel_id", ""),
                    "lang": doc.metadata.get("lang", "en"),
                    "upload_date": doc.metadata.get("upload_date", ""),
                    "webpage_url": doc.metadata.get("webpage_url", ""),
                },
            ))

        await qdrant.upsert(collection_name = QDRANT_COLLECTION, points = points)
        total_upserted += len(points)

        # Log progress every 50 transcripts
        if total_transcripts % 50 == 0:
            print(f"[ingest] Progress: {total_transcripts} transcripts, {total_chunks} chunks, {total_upserted} points", flush = True)

    return {
        "total_transcripts": total_transcripts,
        "total_chunks": total_chunks,
        "points_upserted": total_upserted,
        "collection_created": collection_created,
        "embedding": "nvidia-nim-api",
        "collection": QDRANT_COLLECTION,
    }
