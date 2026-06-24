"""ycs/rag/standard/nodes/generate — GENERATE node.

Stitches retrieved documents into a `[Video: title]` header + content
block per source, then invokes the generation chain. `<think>` blocks
from reasoning models are stripped before returning.

Direct port of deprecated `graphs/youtube/rag.py:L80-101`."""
from __future__ import annotations

import asyncio
from domains.ycs.runtime.observability import traced

from ....domain import history_to_messages, strip_think_tags
from ...state import YouTubeRAGState
from .prompts import GENERATE_PROMPT


# Hard ceiling on a single generation call. Beats the rotator's natural
# retries: at this point the LiteLLM Router has already cycled through
# its catalog. If we're still waiting at 180 s the most likely cause is
# a hung connection that the SDK isn't classifying as a TimeoutError —
# better to surface a clear error in the streamed `generation` than to
# leave the frontend's Generate stage spinning forever.
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
