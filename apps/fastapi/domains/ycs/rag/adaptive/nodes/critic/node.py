"""ycs/rag/adaptive/nodes/critic — DEEP-path CRITIC node.

Validates the synthesis against sub-research evidence. On structured-
output failure we default to confidence=0.5 + grounded=True so the
caller still receives a usable envelope (deprecated rationale: prefer
graceful degradation over total failure for DEEP mode).

Direct port of deprecated `graphs/youtube/adaptive.py:L313-340`."""
from __future__ import annotations

from domains.ycs.runtime.observability import traced

from ...params import CRITIC_FALLBACK_CONFIDENCE
from ...state import AdaptiveRAGState
from .prompts import CRITIC_PROMPT
from .schemas import CriticAssessment


@traced("rag.critic")
async def critic(state: AdaptiveRAGState, llm) -> dict:
    """LLM-as-critic over the synthesis. Returns
    (confidence_score, grounded) for the response envelope."""
    parts: list[str] = []
    for sr in state.get("sub_results", []):
        parts.append(f"Q: {sr['sub_question']}\nA: {sr['answer']}")
    sub_results_text = "\n---\n".join(parts)

    # 2026-06-11: default `method="json_schema"` — see
    # `standard/nodes/hallucination/node.py` for the rationale.
    chain = CRITIC_PROMPT | llm.with_structured_output(
        CriticAssessment,
    )
    try:
        result = await chain.ainvoke({
            "question":     state["question"],
            "synthesis":    state.get("generation", ""),
            "sub_results":  sub_results_text,
        })
        return {
            "confidence_score": result.confidence_score,
            "grounded":         result.claims_supported,
        }
    except Exception:
        return {
            "confidence_score": CRITIC_FALLBACK_CONFIDENCE,
            "grounded":         True,
        }
