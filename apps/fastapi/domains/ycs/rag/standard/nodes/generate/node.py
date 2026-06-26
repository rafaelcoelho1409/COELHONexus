"""ycs/rag/standard/nodes/generate — GENERATE node. Formats retrieved docs + calls the LLM chain."""
from __future__ import annotations

import asyncio
from domains.ycs.runtime.observability import traced

from ....domain import history_to_messages, strip_think_tags
from ...state import YouTubeRAGState
from .prompts import GENERATE_PROMPT


# 180s: after the Router exhausts its catalog, a hung connection won't raise TimeoutError natively.
_GENERATE_TIMEOUT_S = 180.0


@traced("rag.generate")
async def generate(state: YouTubeRAGState, llm) -> dict:
    """Produce an answer using the relevant documents."""
    context_parts: list[str] = []
    for doc in state["documents"]:
        meta = doc.metadata
        header = (
            f"[Video: {meta.get('title', 'Unknown')}] "
            f"({meta.get('webpage_url', '')})"
        )
        context_parts.append(f"{header}\n{doc.page_content}")
    context = "\n\n---\n\n".join(context_parts)

    chain = GENERATE_PROMPT | llm
    try:
        response = await asyncio.wait_for(
            chain.ainvoke({
                "question": state["question"],
                "context":  context,
                "history":  history_to_messages(state.get("conversation_history")),
            }),
            timeout = _GENERATE_TIMEOUT_S,
        )
        return {"generation": strip_think_tags(response.content)}
    except asyncio.TimeoutError:
        return {
            "generation": (
                "The model didn't respond within "
                f"{int(_GENERATE_TIMEOUT_S)}s. The rotator may have "
                "exhausted its retries on a hung deployment — please "
                "retry the question."
            ),
        }
    except Exception as e:
        return {"generation": f"Error generating answer: {e}"}
