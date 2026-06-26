"""ycs/rag/adaptive/nodes/plan — DEEP-path PLAN node.

If the classifier already emitted `sub_questions`, just stamp a
research-plan summary. Otherwise run the fallback planner LLM.
The "Fallback: generic pattern
analysis" path was firing on almost every DEEP run during free-tier
rate-pressure storms (Gemini 429 → planner gives up → 3 generic
sub-questions). One explicit retry lets the rotator pick a fresh
arm before we degrade the user's plan to a stub."""
from __future__ import annotations

import asyncio
import logging

from domains.ycs.runtime.observability import traced

from ....domain import parse_json_model_output
from ...state import AdaptiveRAGState
from .prompts import PLAN_FALLBACK_PROMPT
from .schemas import ResearchPlan


logger = logging.getLogger(__name__)

# 180 s ceiling on the fallback planner LLM, per attempt. Bumped
# from 90 s after observing the planner LLM bouncing through
# multiple Gemini 429s + NIM kimi 429s before getting a fresh arm
# under sustained free-tier rate pressure — 90 s wasn't enough time
# for the rotator's first attempt to cycle through that many arms.
_PLAN_TIMEOUT_S = 180.0

# Number of LLM attempts before falling back to the generic
# pattern-analysis sub-questions. 2 = one initial + one retry. Each
# different arm than attempt #1 in the common case.
_PLAN_MAX_ATTEMPTS = 2


@traced("rag.plan")
async def plan_research(state: AdaptiveRAGState, llm) -> dict:
    """If `sub_questions` exist already, no LLM call. Otherwise fall
    back to the planner prompt."""
    if state.get("sub_questions"):
        n = len(state["sub_questions"])
        return {
            "research_plan": (
                f"Investigating {n} aspects of: {state['question']}"
            ),
        }
    # match classify's plain-JSON strategy. Native
    # structured-output validation can wedge before emitting any graph
    # update; local validation keeps the planner portable across arms.
    chain = PLAN_FALLBACK_PROMPT | llm
    last_exc: BaseException | None = None
    for attempt in range(1, _PLAN_MAX_ATTEMPTS + 1):
        try:
            response = await asyncio.wait_for(
                chain.ainvoke({"question": state["question"]}),
                timeout = _PLAN_TIMEOUT_S,
            )
            result = parse_json_model_output(
                response.content, ResearchPlan,
            )
            # Defensive: a structured-output that parsed cleanly but
            # came back empty is the same failure mode for our
            # purposes — try again before degrading to the generic
            # sub-question stub.
            if result.sub_questions:
                return {
                    "sub_questions": result.sub_questions,
                    "research_plan": result.strategy,
                }
            last_exc = ValueError("planner returned no sub_questions")
        except (asyncio.TimeoutError, Exception) as e:
            last_exc = e
        logger.info(
            f"[ycs:plan] attempt {attempt}/{_PLAN_MAX_ATTEMPTS} "
            f"failed: {type(last_exc).__name__}: {last_exc}"
        )
    # Both attempts blew — degrade gracefully to the generic
    # pattern-analysis sub-questions.
    logger.warning(
        f"[ycs:plan] all {_PLAN_MAX_ATTEMPTS} attempts failed; "
        f"falling back to generic pattern analysis"
    )
    return {
        "sub_questions": [
            f"What patterns emerge regarding: {state['question']}",
            f"What contradictions exist regarding: {state['question']}",
            f"What is frequently repeated about: {state['question']}",
        ],
        "research_plan": "Fallback: generic pattern analysis",
    }
