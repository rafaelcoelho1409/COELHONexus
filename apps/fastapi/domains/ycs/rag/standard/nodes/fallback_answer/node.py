"""ycs/rag/standard/nodes/fallback_answer — no-evidence rescue node.

CRAG-style graceful degradation (Yan et al. 2024). Fires when the
STANDARD pipeline's retrieve → grade → rewrite loop exhausts
`max_retries` with zero documents surviving the strict grader.
Produces a real `generation` from:
  - SOFT EVIDENCE: the pre-grade Neo4j + Qdrant retrieval pool
    accumulated across rewrite rounds (`pre_grade_documents`). These
    are the closest matches the corpus has — the strict grader
    rejected them as not directly relevant, but they remain useful
    as topical hints.
  - WEB SEARCH (2026-09-16): a best-effort external lookup via
    `domains.ycs.rag.service.search_web` (Parallel's free keyless
    MCP endpoint) when the corpus has nothing — closes genuine corpus
    gaps (topics never covered by any indexed video) instead of
    relying on stale/absent parametric knowledge alone. Deliberately
    scoped to ONLY this node, not the main retrieval fan-out — see
    that module's docstring for the full rationale. Best-effort: an
    empty/failed search degrades silently, never blocks the answer.
  - CONVERSATION HISTORY: prior chat turns (for meta-questions and
    follow-ups).
  - LLM PARAMETRIC KNOWLEDGE: only for widely-known facts.

Plus surfaces the soft-evidence videos as "related videos" citations
in the right-rail so the user can click through. Citations are
deduped by `video_id` (mirrors `cite/node.py`'s policy). Web results
are NOT added as citations (no stable per-user-session URL contract
the rail's click-through UX expects) — they're prose context only,
explicitly framed as external in the prompt's output rules."""
from __future__ import annotations
import domains
from domains.ycs.runtime.observability.service import traced
from .... import domain, service
from ... import params, state
from . import prompts

import asyncio

from langchain_core.documents import Document


def _format_soft_evidence(docs: list[Document]) -> str:
    """Render up to `params.FALLBACK_SOFT_EVIDENCE_FOR_PROMPT` docs as a single
    delimited block suitable for the prompt's `{soft_evidence}` slot.
    Returns a "(no candidate matches)" sentinel string when there
    were zero retrievals — keeps the prompt structure stable for
    the LLM (always has SOMETHING in that slot)."""
    slice_ = (docs or [])[:params.FALLBACK_SOFT_EVIDENCE_FOR_PROMPT]
    if not slice_:
        return (
            "(The retriever returned zero candidate documents — "
            "the corpus appears to have no content close to this "
            "question.)"
        )
    parts: list[str] = []
    for doc in slice_:
        meta = getattr(doc, "metadata", None) or {}
        header = (
            f"[Video: {meta.get('title', 'Unknown')}] "
            f"({meta.get('webpage_url', '')})"
        )
        parts.append(f"{header}\n{doc.page_content}")
    return "\n\n---\n\n".join(parts)


def _format_web_context(raw: str) -> str:
    """Mirrors `_format_soft_evidence`'s always-something-in-the-slot
    contract — a stable sentinel when the search returned nothing
    (missing dependency, network failure, rate limit, or a genuinely
    empty result set) keeps the prompt structure predictable for the
    LLM regardless of why it's empty."""
    text = (raw or "").strip()
    if not text:
        return "(no web search results available)"
    return text


def _related_citations(docs: list[Document]) -> list[dict]:
    """Build a citation list from the soft-evidence pool, deduped by
    `video_id`. Same shape as `cite/node.py::format_citations` so the
    UI's right-rail code path doesn't need to know which node emitted
    them."""
    seen: set[str] = set()
    out: list[dict] = []
    for doc in docs or []:
        meta = getattr(doc, "metadata", None) or {}
        video_id = meta.get("video_id", "")
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        out.append({
            "video_id": video_id,
            "title":    meta.get("title", ""),
            "channel":  meta.get("channel", ""),
            "url":      meta.get("webpage_url", ""),
            "source":   meta.get("source", ""),
            "snippet":  (getattr(doc, "page_content", "") or "")[:220].strip(),
        })
        if len(out) >= params.FALLBACK_RELATED_CITATIONS_CAP:
            break
    return out


@traced("rag.fallback_answer")
async def fallback_answer(state: state.YouTubeRAGState, llm) -> dict:
    """Produce a candid answer using soft evidence + history + general
    knowledge when retrieval yields no strict-relevant docs.

    Returns:
      - `generation`: a real string, never empty.
      - `citations`: deduped video cards from the SOFT-EVIDENCE pool
         (surfaced as "related videos" — the rail UI doesn't need to
         distinguish; the answer prose makes the relationship clear).
      - `grounded`: False (explicit — the answer is not strictly
         transcript-grounded; the SSE layer can surface this if/when
         the UI grows a "soft-evidence" badge)."""
    soft_evidence_docs = state.get("pre_grade_documents") or []
    soft_evidence_text = _format_soft_evidence(soft_evidence_docs)
    related_citations  = _related_citations(soft_evidence_docs)
    # Best-effort, short-timeout, never raises — see `search_web`'s
    # own docstring. Runs BEFORE the generation call below (adds to
    # this already-worst-case-latency path), so it stays capped at
    # `web_search._SEARCH_TIMEOUT_S` (15s) independent of the
    # generation call's own 60s budget.
    web_context_text = _format_web_context(
        await service.search_web(
            state["question"], session_id = state.get("thread_id"),
        ),
    )

    chain = prompts.FALLBACK_PROMPT | llm
    try:
        domains.ycs.runtime.llm_counter.service.set_node(node = "fallback_answer")
        response = await service.resilient_ainvoke(
            chain,
            {
                "question":      state["question"],
                "soft_evidence": soft_evidence_text,
                "web_context":   web_context_text,
                "history":       domain.history_to_messages(
                    state.get("conversation_history"),
                ),
            },
            operation    = "fallback_answer",
            timeout_s    = params.FALLBACK_TIMEOUT_S,
            max_attempts = 2,
        )
        return {
            "generation": domain.strip_think_tags(response.content),
            "citations":  related_citations,
            "grounded":   False,
        }
    except asyncio.TimeoutError:
        return {
            "generation": (
                "The strict grader didn't find direct evidence for "
                "your question in the indexed transcripts, and the "
                "fallback generation didn't respond within "
                f"{int(params.FALLBACK_TIMEOUT_S)}s. Please retry — the "
                "rotator may pick a healthier arm next attempt."
            ),
            "citations": related_citations,
            "grounded":  False,
        }
    except Exception as e:
        return {
            "generation": (
                "The strict grader didn't find direct evidence for "
                "your question in the indexed transcripts, and the "
                "fallback generation hit an error: "
                f"`{type(e).__name__}: {e}`. Please retry."
            ),
            "citations": related_citations,
            "grounded":  False,
        }
