"""ycs/rag/standard/nodes/hallucination — CHECK HALLUCINATION node.

LLM-as-judge for factual grounding. Two booleans per
`HallucinationCheck` — both must be true to consider the answer
acceptable. On structured-output error we DEFAULT to grounded=True
(deprecated rationale: assume grounded to avoid blocking the user
on a transient LLM hiccup; the graph still bails after MAX_RETRIES).

Direct port of deprecated `graphs/youtube/rag.py:L104-139`."""
from __future__ import annotations

from domains.ycs.runtime.observability import traced

from ...state import YouTubeRAGState
from .params import MAX_DOC_CHARS
from .prompts import HALLUCINATION_PROMPT
from .schemas import HallucinationCheck


@traced("rag.hallucination")
async def check_hallucination(state: YouTubeRAGState, llm) -> dict:
    """Verify the generation is grounded in documents."""
    doc_texts = [
        doc.page_content[:MAX_DOC_CHARS] for doc in state["documents"]
    ]
    documents_str = "\n---\n".join(doc_texts)

    # 2026-06-11: use the default `method="json_schema"` (cross-
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
        result: HallucinationCheck = await chain.ainvoke({
            "question":   state["question"],
            "generation": state["generation"],
            "documents":  documents_str,
        })
        # AND the two booleans — both must hold for "good enough".
        return {"grounded": result.grounded and result.addresses_question}
    except Exception:
        # Don't block on a transient judge failure. The MAX_RETRIES
        # ceiling stops infinite loops elsewhere.
        return {"grounded": True}
