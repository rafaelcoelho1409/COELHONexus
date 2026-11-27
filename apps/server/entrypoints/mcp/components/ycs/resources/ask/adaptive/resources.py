"""Read-only context resources for YCS Ask."""
from __future__ import annotations

import infra
import domains


class AdaptiveAskResources:
    """Expose available YCS Ask modes and indexed channel scopes."""

    def capabilities(self) -> dict:
        return {
            "application": "ycs",
            "corpus": "Indexed YouTube metadata, transcripts, transcript chunks, and knowledge graph",
            "modes": [
                {"name": "fast", "description": "Quick direct answer, with retrieval when needed."},
                {"name": "standard", "description": "Citation-grounded adaptive RAG over YCS sources."},
                {"name": "deep", "description": "Plan and synthesize parallel research questions."},
                {"name": "automatic", "description": "Let YCS select the route for the question."},
            ],
            "tool": "ycs_ask",
            "citation_fields": ["title", "url", "video_id", "chunk_index"],
            "scope": "Pass channel_ids to ycs_ask to restrict retrieval to selected YouTube channels.",
        }

    async def channels(self) -> dict:
        """Return the most represented indexed channels, with stable IDs."""
        es = infra.elasticsearch.service.get_es()
        with domains.ycs.ask.observability.service.es_search_span(
            index=infra.elasticsearch.keys.INDEX_METADATA,
            top_k=0,
            operation="ask_channels",
        ):
            response = await es.search(
                index=infra.elasticsearch.keys.INDEX_METADATA,
                size=0,
                aggs={
                    "channels": {
                        "terms": {"field": "channel_id", "size": 1_000},
                        "aggs": {
                            "sample": {
                                "top_hits": {
                                    "size": 1,
                                    "_source": ["channel_id", "channel"],
                                },
                            },
                        },
                    },
                },
            )
        buckets = response.get("aggregations", {}).get("channels", {}).get("buckets", [])
        items = []
        for bucket in buckets:
            channel_id = bucket.get("key")
            if not channel_id:
                continue
            hits = bucket.get("sample", {}).get("hits", {}).get("hits", [])
            source = hits[0].get("_source", {}) if hits else {}
            items.append({
                "channel_id": channel_id,
                "channel": source.get("channel") or channel_id,
                "video_count": bucket.get("doc_count", 0),
            })
        items.sort(key=lambda item: (-int(item["video_count"] or 0), item["channel"]))
        return {
            "application": "ycs",
            "channel_count_in_sample": len(items),
            "returned": min(len(items), 100),
            "items_truncated": len(items) > 100,
            "items": items[:100],
        }
