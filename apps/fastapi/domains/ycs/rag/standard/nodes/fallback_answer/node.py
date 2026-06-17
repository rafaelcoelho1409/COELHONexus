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
  - CONVERSATION HISTORY: prior chat turns (for meta-questions and
    follow-ups).
  - LLM PARAMETRIC KNOWLEDGE: only for widely-known facts.

Plus surfaces the soft-evidence videos as "related videos" citations
in the right-rail so the user can click through. Citations are
deduped by `video_id` (mirrors `cite/node.py`'s policy)."""
from __future__ import annotations

import asyncio

from langchain_core.documents import Document

from domains.ycs.runtime.observability import traced

from ....domain import history_to_messages, strip_think_tags
from ...state import YouTubeRAGState
from .prompts import FALLBACK_PROMPT


# Tighter than `generate`'s 180s — soft-evidence prompts run shorter
# context, so the rotator should answer faster. 90s covers the
# cascade across a slow arm + one retry; longer than that is a hung
# deployment we should surface instead of waiting on.
_FALLBACK_TIMEOUT_S = 90.0

# Cap on soft-evidence docs passed into the prompt context. The state
# field is capped at 12 across all rewrite rounds (see
# `retrieve/node.py::_PRE_GRADE_CAP`); this is the per-call slice
# the LLM actually reads. 8 keeps total prompt size near 4 KB so
# every free-tier arm's window stays comfortable.
_SOFT_EVIDENCE_FOR_PROMPT = 8

# Cap on "related videos" citations surfaced in the right-rail. 6
# matches the typical `format_citations` payload size — more would
# overwhelm the rail UI, fewer would feel sparse.
_RELATED_CITATIONS_CAP = 6


def _format_soft_evidence(docs: list[Document]) -> str:
    """Render up to `_SOFT_EVIDENCE_FOR_PROMPT` docs as a single
    delimited block suitable for the prompt's `{soft_evidence}` slot.
    Returns a "(no candidate matches)" sentinel string when there
    were zero retrievals — keeps the prompt structure stable for
    the LLM (always has SOMETHING in that slot)."""
    slice_ = (docs or [])[:_SOFT_EVIDENCE_FOR_PROMPT]
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
        })
        if len(out) >= _RELATED_CITATIONS_CAP:
            break
    return out


@traced("rag.fallback_answer")
async def fallback_answer(state: YouTubeRAGState, llm) -> dict:
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

    chain = FALLBACK_PROMPT | llm
    try:
        response = await asyncio.wait_for(
            chain.ainvoke({
                "question":      state["question"],
                "soft_evidence": soft_evidence_text,
                "history":       history_to_messages(
                    state.get("conversation_history"),
                ),
            }),
            timeout = _FALLBACK_TIMEOUT_S,
        )
        return {
            "generation": strip_think_tags(response.content),
            "citations":  related_citations,
            "grounded":   False,
        }
    except asyncio.TimeoutError:
        return {
            "generation": (
                "The strict grader didn't find direct evidence for "
                "your question in the indexed transcripts, and the "
                "fallback generation didn't respond within "
                f"{int(_FALLBACK_TIMEOUT_S)}s. Please retry — the "
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
