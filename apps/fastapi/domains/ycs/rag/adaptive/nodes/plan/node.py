"""ycs/rag/adaptive/nodes/plan — DEEP-path PLAN node.

If the classifier already emitted `sub_questions`, just stamp a
research-plan summary. Otherwise run the fallback planner LLM.

Direct port of deprecated `graphs/youtube/adaptive.py:L184-224`."""
from __future__ import annotations

from ...state import AdaptiveRAGState
from .prompts import PLAN_FALLBACK_PROMPT
from .schemas import ResearchPlan


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
    chain = PLAN_FALLBACK_PROMPT | llm.with_structured_output(
        ResearchPlan, method = "function_calling",
    )
    try:
        result = await chain.ainvoke({"question": state["question"]})
        return {
            "sub_questions": result.sub_questions,
            "research_plan": result.strategy,
        }
    except Exception:
        # Fallback: generic pattern-analysis sub-questions.
        return {
            "sub_questions": [
                f"What patterns emerge regarding: {state['question']}",
                f"What contradictions exist regarding: {state['question']}",
                f"What is frequently repeated about: {state['question']}",
            ],
            "research_plan": "Fallback: generic pattern analysis",
        }
