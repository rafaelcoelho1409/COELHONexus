"""ycs/rag/standard/nodes/hallucination — CHECK HALLUCINATION node.

LLM-as-judge for factual grounding. Two booleans per
`HallucinationCheck` — both must be true to consider the answer
acceptable. On structured-output error we DEFAULT to grounded=True
(deprecated rationale: assume grounded to avoid blocking the user
on a transient LLM hiccup; the graph still bails after MAX_RETRIES).
"""
from __future__ import annotations

import asyncio

from domains.ycs.runtime.observability import traced

from ...state import YouTubeRAGState
from .params import MAX_DOC_CHARS
from .prompts import HALLUCINATION_PROMPT
from .schemas import HallucinationCheck


# 60 s ceiling on the hallucination judge LLM. Single structured-
# output call with at most ~10 short doc excerpts and the generation
# itself; healthy models return in 5–15 s. 60 s leaves room for one
# rotator fallback inside the call. Matches the sizing of the other
# adaptive-graph node timeouts (`_CLASSIFY_TIMEOUT_S`,
# `_PLAN_TIMEOUT_S`).
_HALLUCINATION_TIMEOUT_S = 60.0


@traced("rag.hallucination")
async def check_hallucination(state: YouTubeRAGState, llm) -> dict:
    """Verify the generation is grounded in documents."""
    doc_texts = [
        doc.page_content[:MAX_DOC_CHARS] for doc in state["documents"]
    ]
    documents_str = "\n---\n".join(doc_texts)

    # use the default `method="json_schema"` (cross-
    # provider portable: sends `response_format={"type":"json_schema",
    # ...}` via the API instead of `tools` + `tool_choice`). Bypasses
    # Groq's server-side tool-call validator that previously rejected
    # responses with string `"true"` instead of boolean true, which
    # made the graph cycle through every model in the pool before the
    # `except Exception → grounded=True` fallback finally fired.
    chain = HALLUCINATION_PROMPT | llm.with_structured_output(
        HallucinationCheck,
    )
    try:
        result: HallucinationCheck = await asyncio.wait_for(
            chain.ainvoke({
                "question":   state["question"],
                "generation": state["generation"],
                "documents":  documents_str,
            }),
            timeout = _HALLUCINATION_TIMEOUT_S,
        )
        # AND the two booleans — both must hold for "good enough".
        return {"grounded": result.grounded and result.addresses_question}
    except (asyncio.TimeoutError, Exception):
        # Don't block on a transient judge failure or hang. The
        # MAX_RETRIES ceiling stops infinite loops elsewhere.
        return {"grounded": True}
