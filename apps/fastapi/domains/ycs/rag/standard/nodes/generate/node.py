"""ycs/rag/standard/nodes/generate — GENERATE node. Formats retrieved docs + calls the LLM chain."""
from __future__ import annotations

import asyncio
from domains.ycs.rag.llm_call import resilient_ainvoke
from domains.ycs.runtime.llm_counter import set_node as _llm_set_node
from domains.ycs.runtime.observability import traced

from ....domain import history_to_messages, strip_think_tags
from ...state import YouTubeRAGState
from .prompts import GENERATE_PROMPT


# 180s: after the Router exhausts its catalog, a hung connection won't raise TimeoutError natively.
_GENERATE_TIMEOUT_S = 180.0

# Context budget (2026-09-15): grader already judged these docs on their
# first 2000 chars (`grader/params.py::PER_DOC_CHAR_CAP`) in FlashRank
# order (best-first) — generate sees the SAME slice, top-weighted, under
# a total cap. Unbounded full-chunk context (12 docs × multi-KB) made
# slow free-tier arms time out at 180s; ~6 top docs ≈ 12KB is enough
# evidence for a grounded answer and completes far faster. Narrow work
# units = the Planner throughput lesson applied to Ask.
_GENERATE_PER_DOC_CHARS = 2000
_GENERATE_TOTAL_CHARS = 12000


@traced("rag.generate")
async def generate(state: YouTubeRAGState, llm) -> dict:
    """Produce an answer using the relevant documents."""
    context_parts: list[str] = []
    budget = _GENERATE_TOTAL_CHARS
    for doc in state["documents"]:
        if budget <= 0:
            break
        meta = doc.metadata
        header = (
            f"[Video: {meta.get('title', 'Unknown')}] "
            f"({meta.get('webpage_url', '')})"
        )
        body = (doc.page_content or "")[:min(_GENERATE_PER_DOC_CHARS, budget)]
        if not body.strip():
            continue
        context_parts.append(f"{header}\n{body}")
        budget -= len(body)
    context = "\n\n---\n\n".join(context_parts)

    chain = GENERATE_PROMPT | llm
    try:
        _llm_set_node(node = "generate")
        response = await resilient_ainvoke(
            chain,
            {
                "question": state["question"],
                "context":  context,
                "history":  history_to_messages(state.get("conversation_history")),
            },
            operation = "generate",
            timeout_s = _GENERATE_TIMEOUT_S,
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
