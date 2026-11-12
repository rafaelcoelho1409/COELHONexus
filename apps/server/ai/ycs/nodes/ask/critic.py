"""DeepAgent-backed evidence critic node for the YCS adaptive graph."""
from __future__ import annotations

import asyncio
import json

from .... import ycs
import domains


async def critic(state: dict, model) -> dict:
    domains.settings.llm_counter.service.set_node(node = "critic")
    params = ycs.agents.ask.research_critic.params
    evidence = [
        {
            "question": str(item.get("sub_question", "")),
            "answer": str(item.get("answer", ""))[:4000],
            "citations": item.get("citations", []),
        }
        for item in state.get("sub_results", [])
        if isinstance(item, dict)
    ]
    payload = json.dumps(
        {
            "question": str(state.get("question", ""))[:2000],
            "answer": str(state.get("generation", ""))[:params.MAX_SYNTHESIS_CHARS],
            "evidence": evidence,
        },
        ensure_ascii = False,
    )[:params.MAX_EVIDENCE_CHARS]
    try:
        agent = ycs.agents.ask.research_critic.agent.research_critic(model)
        result = await asyncio.wait_for(
            ycs.agents.service.ask(
                agent,
                ycs.agents.ask.research_critic.schemas.CriticAssessment,
                payload,
                attempts = 2,
            ),
            timeout = params.AGENT_TIMEOUT_S,
        )
    except Exception:
        # A reviewer failure is unknown evidence status, never a positive verification.
        return {
            "generation": "I could not verify a reliable answer from the retrieved video evidence. Try a narrower question.",
            "citations": [],
            "confidence_score": 0.0,
            "grounded": False,
        }
    if not result.claims_supported:
        return {
            "generation": "The retrieved video evidence did not support a reliable answer to this question.",
            "citations": [],
            "confidence_score": result.confidence_score,
            "grounded": False,
        }
    return {
        "confidence_score": result.confidence_score,
        "grounded": True,
    }
