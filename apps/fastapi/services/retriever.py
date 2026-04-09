"""
Elasticsearch Retriever for YouTube Transcripts

CONCEPT: A retriever is just a callable that takes a query and returns list[Document].
LangChain's Document model has two fields:
  - page_content: the text to pass to the LLM
  - metadata: dict of extra info (video_id, title, channel, etc.)

This wraps the existing ES async client to search the transcriptions index.
The metadata index is joined to enrich results with video title, channel, etc.

Phase 2 will add Qdrant semantic search alongside this.
Phase 3 will add Neo4j graph traversal.
"""
from elasticsearch import AsyncElasticsearch
from langchain_core.documents import Document


ES_INDEX_TRANSCRIPTIONS = "coelhonexus-youtube-transcriptions"
ES_INDEX_METADATA = "coelhonexus-youtube-metadata"


class ElasticsearchRetriever:
    """Full-text search over YouTube transcriptions in Elasticsearch."""

    def __init__(self, es_client: AsyncElasticsearch, top_k: int = 10):
        self.es = es_client
        self.top_k = top_k

    async def retrieve(self, query: str) -> list[Document]:
        """
        Search transcriptions using ES full-text search.
        Returns Documents enriched with video metadata.
        """
        # Search transcriptions by content
        results = await self.es.search(
            index=ES_INDEX_TRANSCRIPTIONS,
            query={
                "multi_match": {
                    "query": query,
                    "fields": ["content"],
                    "type": "best_fields",
                }
            },
            size=self.top_k,
            _source=["video_id", "lang", "content", "channel_id"],
        )
        hits = results["hits"]["hits"]
        if not hits:
            return []

        # Collect video IDs to fetch metadata
        video_ids = list({h["_source"]["video_id"] for h in hits})

        # Batch fetch metadata for all matched videos
        metadata_map = await self._fetch_metadata(video_ids)

        # Build Document objects
        documents = []
        for hit in hits:
            src = hit["_source"]
            video_id = src["video_id"]
            meta = metadata_map.get(video_id, {})
            documents.append(Document(
                page_content=src.get("content", ""),
                metadata={
                    "video_id": video_id,
                    "lang": src.get("lang", "en"),
                    "title": meta.get("title", ""),
                    "channel": meta.get("channel", ""),
                    "channel_id": src.get("channel_id", ""),
                    "upload_date": meta.get("upload_date", ""),
                    "webpage_url": meta.get("webpage_url", ""),
                    "score": hit["_score"],
                    "source": "elasticsearch",
                },
            ))
        return documents

    async def _fetch_metadata(self, video_ids: list[str]) -> dict:
        """Fetch video metadata from ES metadata index by video IDs."""
        if not video_ids:
            return {}
        results = await self.es.search(
            index=ES_INDEX_METADATA,
            query={"ids": {"values": video_ids}},
            size=len(video_ids),
            _source=["title", "channel", "upload_date", "webpage_url", "description"],
        )
        return {
            h["_id"]: h["_source"]
            for h in results["hits"]["hits"]
        }
