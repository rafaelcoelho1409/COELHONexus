"""ycs/rag/adaptive/nodes/critic — DEEP-path CRITIC node.

Validates the synthesis against sub-research evidence. On structured-
output failure we default to confidence=0.5 + grounded=True so the
caller still receives a usable envelope (deprecated rationale: prefer
graceful degradation over total failure for DEEP mode).
"""
from __future__ import annotations

import asyncio

from domains.ycs.rag.llm_call import resilient_ainvoke
from domains.ycs.runtime.llm_counter import set_node as _llm_set_node
from domains.ycs.runtime.observability import traced

from ...params import CRITIC_FALLBACK_CONFIDENCE
from ...state import AdaptiveRAGState
from .prompts import CRITIC_PROMPT
from .schemas import CriticAssessment


# 2026-09-15: 90 → 60s tiering — failure falls back to
# confidence=0.5 + grounded=True, so fail fast.
_CRITIC_TIMEOUT_S = 60.0


@traced("rag.critic")
async def critic(state: AdaptiveRAGState, llm) -> dict:
    """LLM-as-critic over the synthesis. Returns
    (confidence_score, grounded) for the response envelope."""
    parts: list[str] = []
    for sr in state.get("sub_results", []):
        parts.append(f"Q: {sr['sub_question']}\nA: {sr['answer']}")
    sub_results_text = "\n---\n".join(parts)

    # default `method="json_schema"` — see
    # `standard/nodes/hallucination/node.py` for the rationale.
    chain = CRITIC_PROMPT | llm.with_structured_output(
        CriticAssessment,
    )
    try:
        _llm_set_node(node = "critic")
        result = await resilient_ainvoke(
            chain,
            {
                "question":     state["question"],
                "synthesis":    state.get("generation", ""),
                "sub_results":  sub_results_text,
            },
            operation    = "critic",
            timeout_s    = _CRITIC_TIMEOUT_S,
            max_attempts = 2,
        )
        return {
            "confidence_score": result.confidence_score,
            "grounded":         result.claims_supported,
        }
    except (asyncio.TimeoutError, Exception):
        return {
            "confidence_score": CRITIC_FALLBACK_CONFIDENCE,
            "grounded":         True,
        }
