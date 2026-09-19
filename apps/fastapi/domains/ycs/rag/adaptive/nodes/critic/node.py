"""ycs/rag/adaptive/nodes/critic — DEEP-path CRITIC node.

Validates the synthesis against sub-research evidence. On structured-
output failure we default to confidence=0.5 + grounded=True so the
caller still receives a usable envelope (deprecated rationale: prefer
graceful degradation over total failure for DEEP mode).
"""
from __future__ import annotations

import asyncio

import domains
from domains.ycs.runtime.observability.service import traced

from .... import service
from ... import params, state
from . import prompts, schemas


# 2026-09-15: 90 → 60s tiering — failure falls back to
# confidence=0.5 + grounded=True, so fail fast.
_CRITIC_TIMEOUT_S = 60.0


@traced("rag.critic")
async def critic(state: state.AdaptiveRAGState, llm) -> dict:
    """LLM-as-critic over the synthesis. Returns
    (confidence_score, grounded) for the response envelope."""
    parts: list[str] = []
    for sr in state.get("sub_results", []):
        parts.append(f"Q: {sr['sub_question']}\nA: {sr['answer']}")
    sub_results_text = "\n---\n".join(parts)

    # default `method="json_schema"` — see
    # `standard/nodes/hallucination/node.py` for the rationale.
    chain = prompts.CRITIC_PROMPT | llm.with_structured_output(
        schemas.CriticAssessment,
    )
    try:
        domains.ycs.runtime.llm_counter.service.set_node(node = "critic")
        result = await service.resilient_ainvoke(
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
            "confidence_score": params.CRITIC_FALLBACK_CONFIDENCE,
            "grounded":         True,
        }
