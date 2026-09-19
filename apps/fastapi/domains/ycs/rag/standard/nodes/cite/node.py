"""ycs/rag/standard/nodes/cite — FORMAT CITATIONS node.

Pure projection — no I/O, no LLM. Walks `state["documents"]`,
deduplicates by `video_id`, builds a `{video_id, title, channel, url,
source, snippet}` row per unique source. The frontend renders these as
clickable cards.
"""
from __future__ import annotations

from domains.ycs.runtime.observability.service import traced

from ... import state


# 2026-09-16: /sota-search confirmed a snippet is table-stakes for a
# source card (Perplexity/ChatGPT-search convention ~200 chars). Capped
# client-side too (`renderCitation` in ask.js) as a defensive second
# layer, but the truncation is authored here so the payload itself
# stays small over SSE.
_SNIPPET_CHAR_CAP = 220


@traced("rag.cite")
async def format_citations(state: state.YouTubeRAGState) -> dict:
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
            "snippet":  (doc.page_content or "")[:_SNIPPET_CHAR_CAP].strip(),
        })
    return {"citations": citations}
