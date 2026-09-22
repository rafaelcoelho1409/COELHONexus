"""ycs/rag/standard/nodes/generate — GENERATE node. Formats retrieved docs + calls the LLM chain."""
from __future__ import annotations
import domains
from domains.ycs.runtime.observability.service import traced
from .... import domain, service
from ... import params, state
from . import prompts

import asyncio


@traced("rag.generate")
async def generate(state: state.YouTubeRAGState, llm) -> dict:
    """Produce an answer using the relevant documents."""
    context_parts: list[str] = []
    budget = params.GENERATE_TOTAL_CHARS
    for doc in state["documents"]:
        if budget <= 0:
            break
        meta = doc.metadata
        header = (
            f"[Video: {meta.get('title', 'Unknown')}] "
            f"({meta.get('webpage_url', '')})"
        )
        body = (doc.page_content or "")[:min(params.GENERATE_PER_DOC_CHARS, budget)]
        if not body.strip():
            continue
        context_parts.append(f"{header}\n{body}")
        budget -= len(body)
    context = "\n\n---\n\n".join(context_parts)

    chain = prompts.GENERATE_PROMPT | llm
    try:
        domains.ycs.runtime.llm_counter.service.set_node(node = "generate")
        response = await service.resilient_ainvoke(
            chain,
            {
                "question": state["question"],
                "context":  context,
                "history":  domain.history_to_messages(state.get("conversation_history")),
            },
            operation = "generate",
            timeout_s = params.GENERATE_TIMEOUT_S,
        )
        return {"generation": domain.strip_think_tags(response.content)}
    except asyncio.TimeoutError:
        return {
            "generation": (
                "The model didn't respond within "
                f"{int(params.GENERATE_TIMEOUT_S)}s. The rotator may have "
                "exhausted its retries on a hung deployment — please "
                "retry the question."
            ),
        }
    except Exception as e:
        return {"generation": f"Error generating answer: {e}"}
