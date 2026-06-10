"""ycs/rag/standard/nodes/generate — GENERATE node.

Stitches retrieved documents into a `[Video: title]` header + content
block per source, then invokes the generation chain. `<think>` blocks
from reasoning models are stripped before returning.

Direct port of deprecated `graphs/youtube/rag.py:L80-101`."""
from __future__ import annotations

from domains.ycs.runtime.observability import traced

from ....domain import strip_think_tags
from ...state import YouTubeRAGState
from .prompts import GENERATE_PROMPT


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
        response = await chain.ainvoke({
            "question": state["question"],
            "context":  context,
        })
        return {"generation": strip_think_tags(response.content)}
    except Exception as e:
        return {"generation": f"Error generating answer: {e}"}
