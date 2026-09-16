"""ycs/rag/adaptive/nodes/synthesize — DEEP-path SYNTHESIZE node.

Merges sub-agent results into a unified analytical report. Citations
from all sub-results are deduped by `video_id`; retrieval sources are
union'd.
"""
from __future__ import annotations

import asyncio

from domains.ycs.rag.llm_call import resilient_ainvoke
from domains.ycs.runtime.llm_counter import set_node as _llm_set_node
from domains.ycs.runtime.observability import traced

from ....domain import history_to_messages, strip_think_tags
from ...state import AdaptiveRAGState
from .prompts import SYNTHESIZE_PROMPT


# DEEP synthesis takes a long-context input (every sub-question's
# answer concatenated) so it's the slowest single LLM call in the
# graph. 240 s ceiling leaves headroom over a real long-context
# completion while still capping the dead-arm wait.
_SYNTHESIZE_TIMEOUT_S = 240.0


@traced("rag.synthesize")
async def synthesize(state: AdaptiveRAGState, llm) -> dict:
    """Merge sub-results into one report + deduped citations."""
    parts: list[str] = []
    for i, sr in enumerate(state.get("sub_results", []), 1):
        err = sr.get("error_kind") or ""
        if err:
            ans = f"[FAILED — {err}] {sr.get('answer') or '(no answer)'}"
        else:
            ans = sr.get("answer", "")
        parts.append(
            f"### Sub-question {i}: {sr['sub_question']}\n"
            f"**Answer:** {ans}\n"
            f"**Grounded:** {sr['grounded']}\n"
            f"**Sources:** {', '.join(sr.get('retrieval_sources', []))}"
        )
    n_sub = len(state.get("sub_results", []))
    n_fail = sum(1 for sr in state.get("sub_results", []) if sr.get("error_kind"))
    if n_fail:
        parts.append(
            f"\n_\u26a0 {n_fail}/{n_sub} sub-questions failed — flag "
            f"the gap(s) explicitly in the synthesis; don't paper over "
            f"them. The critic reads this too._"
        )
    sub_results_text = "\n\n".join(parts)

    chain = SYNTHESIZE_PROMPT | llm
    try:
        _llm_set_node(node = "synthesize")
        response = await resilient_ainvoke(
            chain,
            {
                "question":       state["question"],
                "research_plan":  state.get("research_plan", ""),
                "sub_results":    sub_results_text,
                "history":        history_to_messages(
                    state.get("conversation_history"),
                ),
            },
            operation = "synthesize",
            timeout_s = _SYNTHESIZE_TIMEOUT_S,
        )
        generation = strip_think_tags(response.content)
    except asyncio.TimeoutError:
        generation = (
            f"Synthesis didn't complete within {int(_SYNTHESIZE_TIMEOUT_S)}s — "
            f"the long-context model on the rotator pool is hung. "
            f"Please retry."
        )
    except Exception as e:
        generation = f"Synthesis error: {e}"

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
