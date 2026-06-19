"""ycs/retriever — ES full-text retriever (Phase 1).

Imperative Shell: single ES multi_match over the transcript index +
secondary metadata fetch + LangChain Document projection. Channel
scope handled as an ES bool/terms filter.

Direct port of deprecated `services/youtube/retriever.py:L42-109`."""
from __future__ import annotations

from elasticsearch import AsyncElasticsearch
from langchain_core.documents import Document

from domains.ycs.runtime.observability import es_search_span
from infra.elasticsearch import INDEX_METADATA, INDEX_TRANSCRIPTIONS

from .params import ES_DEFAULT_TOP_K


class ElasticsearchRetriever:
    """Full-text search over the YCS transcripts index. Same
    `retrieve(query, channel_ids)` interface as the other three
    retrievers so the SmartRetriever can fan out uniformly."""

    def __init__(
        self,
        es_client: AsyncElasticsearch,
        top_k: int = ES_DEFAULT_TOP_K,
    ) -> None:
        self.es = es_client
        self.top_k = top_k

    async def retrieve(
        self, query: str, channel_ids: list[str] | None = None,
    ) -> list[Document]:
        # Build the ES query — wrap multi_match in a bool with a terms
        # filter ONLY when channel_ids was supplied. The two branches
        # are kept separate per deprecated (vs always-wrapped) so the
        # ES query planner gets the simpler form on the common path.
        es_query: dict
        if channel_ids:
            es_query = {
                "bool": {
                    "must": {
                        "multi_match": {
                            "query":  query,
                            "fields": ["content"],
                            "type":   "best_fields",
                        },
                    },
                    "filter": {"terms": {"channel_id": channel_ids}},
                },
            }
        else:
            es_query = {
                "multi_match": {
                    "query":  query,
                    "fields": ["content"],
                    "type":   "best_fields",
                },
            }

        with es_search_span(
            index                = INDEX_TRANSCRIPTIONS,
            top_k                = self.top_k,
            channel_filter_count = len(channel_ids) if channel_ids else 0,
        ):
            results = await self.es.search(
                index = INDEX_TRANSCRIPTIONS,
                query = es_query,
                size = self.top_k,
                _source = ["video_id", "lang", "content", "channel_id"],
            )
        hits = results["hits"]["hits"]
        if not hits:
            return []

        video_ids = list({h["_source"]["video_id"] for h in hits})
        metadata_map = await self._fetch_metadata(video_ids)

        documents: list[Document] = []
        for hit in hits:
            src = hit["_source"]
            video_id = src["video_id"]
            meta = metadata_map.get(video_id, {})
            documents.append(Document(
                page_content = src.get("content", ""),
                metadata = {
                    "video_id":    video_id,
                    "lang":        src.get("lang", "en"),
                    "title":       meta.get("title", ""),
                    "channel":     meta.get("channel", ""),
                    "channel_id":  src.get("channel_id", ""),
                    "upload_date": meta.get("upload_date", ""),
                    "webpage_url": meta.get("webpage_url", ""),
                    "score":       hit["_score"],
                    "source":      "elasticsearch",
                },
            ))
        return documents

    async def _fetch_metadata(self, video_ids: list[str]) -> dict:
        """Secondary fetch from the metadata index — the transcripts
        index only carries video_id (denormalized) + lang + content +
        channel_id, so titles / channels / upload_date / urls live in
        the separate metadata index."""
        if not video_ids:
            return {}
        with es_search_span(
            index                = INDEX_METADATA,
            top_k                = len(video_ids),
            operation            = "metadata_lookup",
        ):
            results = await self.es.search(
                index = INDEX_METADATA,
                query = {"ids": {"values": video_ids}},
                size = len(video_ids),
                _source = ["title", "channel", "upload_date", "webpage_url"],
            )
        return {
            h["_id"]: h["_source"]
            for h in results["hits"]["hits"]
        }
