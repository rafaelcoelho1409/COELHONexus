"""ycs/rag/standard/nodes/retrieve — RETRIEVE node.

Calls into `SmartRetriever.retrieve(query, channel_ids)` and records
which retrieval arms contributed (qdrant_hybrid / neo4j_graph /
elasticsearch). The `retrieval_sources` field feeds the SSE stream
+ the final response envelope.
Persisting these BEFORE grading is the
only way to surface them later: `grade_documents` replaces
`state["documents"]` with the filtered subset, losing the rejected
candidates forever otherwise."""
from __future__ import annotations

from langchain_core.documents import Document

import domains
from domains.ycs.runtime.observability.service import traced

from ... import state


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
    state: state.YouTubeRAGState,
    retriever,
    channel_ids: list[str] | None = None,
) -> dict:
    """Search for documents matching the query. `channel_ids` is passed
    via closure from the parent graph build to scope retrieval.

    2026-09-15 dual-query: the rewritten `search_query` is primary and
    the original `question` rides along as the free second variant
    (deduped inside the retriever when identical, e.g. first turns) —
    same gather, no extra serial latency."""
    query = state.get("search_query") or state["question"]
    extra = [state["question"]] if state.get("question") else []
    # 2026-09-15 per-request arm breaker (see SmartRetriever): a FAILED
    # arm is skipped for the rest of this request; a clean-EMPTY arm is
    # skipped after 2 consecutive empties. Qdrant is never skipped.
    # Streaks live in state so rewrite rounds accumulate the evidence.
    skipped = set(state.get("skipped_arms") or [])
    streaks = dict(state.get("arm_empty_streaks") or {})
    try:
        documents, arm_status = await retriever.retrieve_detailed(
            query, channel_ids, extra_queries = extra,
            skip_arms = frozenset(skipped),
        )
    except Exception:
        documents, arm_status = [], {}
    for arm, status in (arm_status or {}).items():
        if arm == "qdrant" or arm in skipped:
            continue
        if status == "failed" or (
            status == "empty" and streaks.get(arm, 0) + 1 >= 2
        ):
            skipped.add(arm)
            if status != "failed":
                streaks.pop(arm, None)
        elif status == "empty":
            streaks[arm] = streaks.get(arm, 0) + 1
        elif status == "ok":
            streaks.pop(arm, None)
    sources = list({
        doc.metadata.get("source", "unknown") for doc in documents
    })
    domains.ycs.runtime.observability.metrics.record_retrieved_docs(
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
        "skipped_arms":        sorted(skipped),
        "arm_empty_streaks":   streaks,
    }
