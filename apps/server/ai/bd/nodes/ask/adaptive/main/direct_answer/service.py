"""Single-call conversational replies that do not claim to represent book content."""
from __future__ import annotations
import domains
from ...... import graphs, prompts

import asyncio
import json

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime


async def direct_answer(
    state: dict,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    """Answer greetings and other corpus-free turns with recent context."""
    domains.settings.llm_counter.service.set_node(node = "direct_answer")
    payload = {
        "message": str(state.get("question", ""))[:2000],
        "recent_turns": state.get("conversation_history", [])[-4:],
    }
    messages = [
        SystemMessage(
            content = prompts.ask.conversation.prompts.SYSTEM_PROMPT),
        HumanMessage(
            content = json.dumps(
                payload, 
                ensure_ascii = False, 
                default = str)[:6000]),
    ]
    try:
        response = await asyncio.wait_for(
            domains.settings.chat.service.resilient_ainvoke(
                lambda: runtime.context.model.ainvoke(messages),
                attempts = 2,
                wait_base_s = 0.5,
            ),
            timeout = 45.0,
        )
        await domains.settings.llm_counter.service.bump_response(response)
        content = response.content
        if isinstance(content, str):
            answer = content.strip()
        elif isinstance(content, list):
            answer = "\n".join(
                part["text"] for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ).strip()
        else:
            answer = ""
        if not answer:
            raise ValueError("the model returned an empty conversational reply")
        return {
            "generation": answer,
            "grounded": False,
            "citations": [],
            "retrieval_sources": [],
            "verification_status": "not_applicable",
        }
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 — keep the persisted stream contract on model failures.
        return {
            "generation": f"Error generating answer: {type(error).__name__}: {str(error)[:180]}",
            "grounded": False,
            "citations": [],
            "verification_status": "not_applicable",
        }
