"""
Dual Ingestion Pipeline — ES Transcripts → Qdrant Hybrid Collection

CONCEPT: This pipeline bridges your existing data (ES) with the new vector store (Qdrant).
It reads transcripts from Elasticsearch, chunks them, generates both dense and sparse
embeddings, and upserts to a Qdrant collection configured for hybrid search.

Flow:
  ES (transcriptions index) → Read transcripts
    → Chunk (RecursiveCharacterTextSplitter)
    → Embed (dense: bge-base + sparse: BM25)
    → Upsert to Qdrant collection "youtube-transcripts"

The Qdrant collection stores BOTH vector types:
- "dense": semantic embeddings from transformer model
- "sparse": BM25 keyword embeddings from FastEmbed

This enables hybrid search: one Qdrant query returns results ranked by
both semantic similarity AND keyword matching, with automatic score fusion.

Phase 3 will extend this to also extract entities into Neo4j.
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
    """
    Generate a deterministic point ID for Qdrant.

    CONCEPT: Using a hash of video_id + chunk_index means:
    - Re-ingesting the same video overwrites existing points (idempotent)
    - No duplicate points for the same content
    - IDs are consistent across runs
    """
    raw = f"{video_id}_{chunk_index}"
    return hashlib.md5(raw.encode()).hexdigest()


async def ensure_collection(
    qdrant: AsyncQdrantClient,
    dense_dimensions: int,
) -> bool:
    """
    Create the Qdrant collection if it doesn't exist.

    CONCEPT: A Qdrant collection needs vector configuration upfront:
    - "dense": the semantic vector config (size, distance metric)
    - "sparse": the BM25 sparse vector config (no fixed size — varies per doc)

    Cosine distance is standard for normalized embeddings.
    """
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


async def fetch_transcripts_from_es(
    es: AsyncElasticsearch,
    video_ids: list[str] | None = None,
    batch_size: int = 100,
) -> list[dict]:
    """
    Fetch transcripts from ES. If video_ids is None, fetches all.

    Returns list of dicts with: video_id, content, lang, channel_id
    """
    if video_ids:
        query = {"terms": {"video_id": video_ids}}
    else:
        query = {"match_all": {}}
    results = []
    # Use search with scroll for large result sets
    response = await es.search(
        index = ES_INDEX_TRANSCRIPTIONS,
        query = query,
        size = batch_size,
        _source = ["video_id", "content", "lang", "channel_id"],
    )
    results.extend([h["_source"] for h in response["hits"]["hits"]])
    return results


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


async def ingest_to_qdrant(
    es: AsyncElasticsearch,
    qdrant: AsyncQdrantClient,
    video_ids: list[str] | None = None,
    embedding_model: str = "bge-base",
    chunk_size: int = 2000,
    chunk_overlap: int = 200,
) -> dict:
    """
    Full ingestion pipeline: ES → Chunk → Embed → Qdrant.

    CONCEPT: This is a batch pipeline, not a streaming one.
    For production with thousands of videos, you'd run this as a background
    task or Airflow DAG. For development, it runs synchronously per request.

    Returns stats: {total_transcripts, total_chunks, points_upserted, collection_created}
    """
    # 1. Initialize embedding models
    dense_embeddings = create_dense_embeddings(embedding_model)
    sparse_embeddings = create_sparse_embeddings()
    dimensions = get_embedding_dimensions(embedding_model)
    # 2. Ensure Qdrant collection exists
    collection_created = await ensure_collection(qdrant, dimensions)
    # 3. Fetch transcripts from ES
    transcripts = await fetch_transcripts_from_es(es, video_ids)
    if not transcripts:
        return {
            "total_transcripts": 0,
            "total_chunks": 0,
            "points_upserted": 0,
            "collection_created": collection_created,
        }
    # 4. Fetch metadata for all videos
    all_video_ids = list({t["video_id"] for t in transcripts})
    metadata_map = await fetch_metadata_from_es(es, all_video_ids)
    # 5. Chunk all transcripts
    chunker = create_chunker(chunk_size, chunk_overlap)
    all_chunks: list[Document] = []
    for transcript in transcripts:
        vid = transcript["video_id"]
        meta = metadata_map.get(vid, {})
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
        all_chunks.extend(chunks)
    if not all_chunks:
        return {
            "total_transcripts": len(transcripts),
            "total_chunks": 0,
            "points_upserted": 0,
            "collection_created": collection_created,
        }
    # 6. Generate embeddings (dense + sparse)
    texts = [doc.page_content for doc in all_chunks]
    # Dense embeddings (batch)
    dense_vectors = dense_embeddings.embed_documents(texts)
    # Sparse embeddings (batch)
    sparse_vectors = list(sparse_embeddings.embed_documents(texts))
    # 7. Build Qdrant points and upsert in batches
    BATCH_SIZE = 100
    total_upserted = 0
    for batch_start in range(0, len(all_chunks), BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, len(all_chunks))
        points = []
        for i in range(batch_start, batch_end):
            doc = all_chunks[i]
            point_id = _deterministic_id(
                doc.metadata["video_id"],
                doc.metadata["chunk_index"],
            )
            # Build sparse vector from FastEmbed output
            sparse_vec = sparse_vectors[i]
            points.append(PointStruct(
                id = point_id,
                vector = {
                    "dense": dense_vectors[i],
                    "sparse": models.SparseVector(
                        indices = sparse_vec.indices.tolist(),
                        values = sparse_vec.values.tolist(),
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
        await qdrant.upsert(
            collection_name = QDRANT_COLLECTION,
            points = points,
        )
        total_upserted += len(points)
    return {
        "total_transcripts": len(transcripts),
        "total_chunks": len(all_chunks),
        "points_upserted": total_upserted,
        "collection_created": collection_created,
        "embedding_model": embedding_model,
        "collection": QDRANT_COLLECTION,
    }
