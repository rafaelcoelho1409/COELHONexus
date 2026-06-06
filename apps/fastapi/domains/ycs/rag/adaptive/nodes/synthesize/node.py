"""ycs/rag/adaptive/nodes/synthesize — DEEP-path SYNTHESIZE node.

Merges sub-agent results into a unified analytical report. Citations
from all sub-results are deduped by `video_id`; retrieval sources are
union'd.

Direct port of deprecated `graphs/youtube/adaptive.py:L267-311`."""
from __future__ import annotations

from ....domain import strip_think_tags
from ...state import AdaptiveRAGState
from .prompts import SYNTHESIZE_PROMPT


async def synthesize(state: AdaptiveRAGState, llm) -> dict:
    """Merge sub-results into one report + deduped citations."""
    parts: list[str] = []
    for i, sr in enumerate(state.get("sub_results", []), 1):
        parts.append(
            f"### Sub-question {i}: {sr['sub_question']}\n"
            f"**Answer:** {sr['answer']}\n"
            f"**Grounded:** {sr['grounded']}\n"
            f"**Sources:** {', '.join(sr.get('retrieval_sources', []))}"
        )
    sub_results_text = "\n\n".join(parts)

    chain = SYNTHESIZE_PROMPT | llm
    try:
        response = await chain.ainvoke({
            "question":       state["question"],
            "research_plan":  state.get("research_plan", ""),
            "sub_results":    sub_results_text,
        })
        generation = strip_think_tags(response.content)
    except Exception as e:
        generation = f"Synthesis error: {e}"

    # Merge + dedupe citations from all sub-results.
    seen_videos: set[str] = set()
    merged_citations: list[dict] = []
    all_sources: set[str] = set()
    for sr in state.get("sub_results", []):
        for cit in sr.get("citations", []):
            vid = cit.get("video_id", "")
            if vid and vid not in seen_videos:
                seen_videos.add(vid)
                merged_citations.append(cit)
        for src in sr.get("retrieval_sources", []):
            all_sources.add(src)
    return {
        "generation":        generation,
        "citations":         merged_citations,
        "retrieval_sources": list(all_sources),
    }
