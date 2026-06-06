"""ycs/rag/standard/nodes/cite — FORMAT CITATIONS node.

Pure projection — no I/O, no LLM. Walks `state["documents"]`,
deduplicates by `video_id`, builds a `{video_id, title, channel, url,
source}` row per unique source. The frontend renders these as
clickable cards.

Direct port of deprecated `graphs/youtube/rag.py:L142-167`."""
from __future__ import annotations

from ...state import YouTubeRAGState


async def format_citations(state: YouTubeRAGState) -> dict:
    """Extract structured citations from documents (deduped by video_id)."""
    seen_videos: set[str] = set()
    citations: list[dict] = []
    for doc in state["documents"]:
        meta = doc.metadata
        video_id = meta.get("video_id", "")
        if not video_id or video_id in seen_videos:
            continue
        seen_videos.add(video_id)
        citations.append({
            "video_id": video_id,
            "title":    meta.get("title", ""),
            "channel":  meta.get("channel", ""),
            "url":      meta.get("webpage_url", ""),
            "source":   meta.get("source", ""),
        })
    return {"citations": citations}
