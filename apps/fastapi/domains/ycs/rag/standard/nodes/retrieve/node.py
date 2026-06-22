"""ycs/rag/standard/nodes/retrieve — RETRIEVE node.

Calls into `SmartRetriever.retrieve(query, channel_ids)` and records
which retrieval arms contributed (qdrant_hybrid / neo4j_graph /
elasticsearch). The `retrieval_sources` field feeds the SSE stream
+ the final response envelope.

Direct port of deprecated `graphs/youtube/rag.py:L48-67`,
extended 2026-06-16 — also accumulates the pre-grade retrieval set
into `pre_grade_documents` (deduped + capped) so the CRAG-style
fallback rescue (`nodes/fallback_answer/`) can use the closest
Neo4j + Qdrant matches as soft evidence when the grader rejects
everything strict-relevant. Persisting these BEFORE grading is the
only way to surface them later: `grade_documents` replaces
`state["documents"]` with the filtered subset, losing the rejected
candidates forever otherwise."""
from __future__ import annotations

from langchain_core.documents import Document

from domains.ycs.runtime.observability import record_retrieved_docs, traced

from ...state import YouTubeRAGState


# Cap on the cross-round pre-grade pool. Sized for the fallback
# prompt's input budget: 12 docs × ~500 chars/doc ≈ 6 KB context, well
# inside every rotator arm's window. Higher cap risks token bloat with
# no recall gain — the retriever ranks within each round, so the
# top-K of each round (typically 8-10) are already the best matches.
_PRE_GRADE_CAP = 12


def _merge_pre_grade(
    existing: list[Document] | None,
    fresh: list[Document],
) -> list[Document]:
    """Concatenate the prior rounds' pre-grade pool with this round's
    fresh retrieval. Dedup key = `(video_id or webpage_url, content
    prefix)` so the same video chunk doesn't surface multiple times
    when both Qdrant and Neo4j happen to return overlapping segments.
    Order-preserving — earlier rounds rank first, latest round fills
    the tail (the rewriter's refined query lands last and shouldn't
    displace round 1's broader matches when the corpus is sparse)."""
    seen: set[tuple[str, str]] = set()
    out: list[Document] = []
    for doc in (existing or []) + (fresh or []):
        meta = getattr(doc, "metadata", None) or {}
        key = (
            meta.get("video_id") or meta.get("webpage_url") or "",
            (doc.page_content or "")[:80],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(doc)
        if len(out) >= _PRE_GRADE_CAP:
            break
    return out


@traced("rag.retrieve")
async def retrieve(
    state: YouTubeRAGState,
    retriever,
    channel_ids: list[str] | None = None,
) -> dict:
    """Search for documents matching the query. `channel_ids` is passed
    via closure from the parent graph build to scope retrieval."""
    query = state.get("search_query") or state["question"]
    try:
        documents = await retriever.retrieve(query, channel_ids)
    except Exception:
        documents = []
    sources = list({
        doc.metadata.get("source", "unknown") for doc in documents
    })
    record_retrieved_docs(
        route = str(state.get("route") or "unknown"),
        mode = str(state.get("mode") or "standard"),
        count = len(documents),
    )
    merged_pre_grade = _merge_pre_grade(
        state.get("pre_grade_documents"), documents,
    )
    return {
        "documents":           documents,
        "pre_grade_documents": merged_pre_grade,
        "retrieval_sources":   sources,
    }
