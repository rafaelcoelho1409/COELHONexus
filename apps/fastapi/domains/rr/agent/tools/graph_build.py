"""Graph-build — pure I/O orchestrator tool.

The deterministic Phase-4 node from the architecture doc. Reads the
ranked top-N from fs, embeds each paper's abstract via the existing LLM
rotator (NIM `llama-nemotron-embed-1b-v2`, 2048d), and persists to
Neo4j + Qdrant via `service.persist_paper`.

No LLM (the rotator's embed model is a separate non-chat path). Wired
into create_deep_agent's `tools=` list so the orchestrator can invoke it
after deep_read finishes.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from langchain_core.tools import tool

# The embedding factory lives in the LLM rotator package — same one DD/YCS
# use for their embedding workloads.
from domains.llm.rotator.chain.service import embed_via_router_async

from ..keys import FS_FILE_TRIAGE_TOPN
from ...entities import NormalizedPaper
from ...service import persist_paper
from .state import fs_read


logger = logging.getLogger(__name__)


@tool
async def graph_build_papers(
    scan_id: str,
    max_concurrency: int = 4,
) -> str:
    """Persist this scan's ranked papers to Neo4j + Qdrant.

    Call this AFTER deep_read has written extractions for each top-N
    paper — graph_build is the I/O sink for the merge graph.

    Args:
        scan_id: Identifier for this radar scan.
        max_concurrency: Cap on simultaneous embed + upsert flights.
            Defaults to 4 — keeps NIM rotator pressure manageable.

    Returns:
        A short summary including the count of papers persisted and the
        count of skips (papers without arxiv_id or with empty abstracts).
    """
    top_n = fs_read(scan_id, FS_FILE_TRIAGE_TOPN)
    if not top_n or not isinstance(top_n, list):
        # diagnostic detail in the warning. Scan
        # d196a862 logged the bare "no top_n — skipping" 8s before a
        # successful retry where total=12; the warning looked like a
        # real failure but was actually the orchestrator's LLM calling
        # graph_build with a malformed / stale scan_id mid-emission.
        # Surface what we actually got so the next regression debug is
        # one log line, not a forensic exercise.
        observed = type(top_n).__name__ if top_n is not None else "None"
        observed_repr = repr(top_n)[:80] if top_n is not None else "None"
        msg = (
            f"[graph_build] scan_id={scan_id} no usable top_n at "
            f"{FS_FILE_TRIAGE_TOPN} (got type={observed} value={observed_repr}) "
            f"— skipping. If the orchestrator immediately retries with the "
            f"correct scan_id this warning is benign; otherwise check that "
            f"triage_candidates wrote top_n.json before this call."
        )
        logger.warning(msg)
        return msg

    sem = asyncio.Semaphore(max(1, int(max_concurrency)))

    results = await asyncio.gather(
        *(_persist_one(sem, item) for item in top_n),
        return_exceptions=True,
    )
    persisted = sum(1 for r in results if r == "ok")
    skipped   = sum(1 for r in results if r == "skipped")
    errors    = sum(1 for r in results if isinstance(r, Exception) or r == "error")

    msg = (
        f"[graph_build] scan_id={scan_id} "
        f"persisted={persisted} skipped={skipped} errors={errors} "
        f"(total={len(top_n)})"
    )
    logger.info(msg)
    # Phase contextvar for LLM-counter attribution (Path A).
    try:
        from ...runtime.llm_counter import set_phase as _set_llm_phase
        _set_llm_phase("graph_build")
    except Exception: pass
    if errors:
        # the tool's return string.
        first_err = next(
            (str(r) for r in results if isinstance(r, Exception)), "unknown"
        )
        logger.warning(f"[graph_build] first error: {first_err[:300]}")
    return msg


async def _persist_one(
    sem: asyncio.Semaphore, item: dict[str, Any],
) -> str:
    """Embed + persist one paper. Returns 'ok' | 'skipped' | 'error'."""
    arxiv_id = item.get("arxiv_id")
    abstract = (item.get("abstract") or "").strip()
    if not arxiv_id:
        return "skipped"
    paper = _dict_to_paper(item)
    embedding: list[float] | None = None
    async with sem:
        try:
            if abstract:
                vecs = await embed_via_router_async([abstract], input_type="passage")
                embedding = vecs[0] if vecs else None
            await persist_paper(paper, embedding=embedding, signal=item.get("signal"))
            return "ok"
        except Exception as e:
            logger.warning(
                f"[graph_build] persist failed for {arxiv_id}: "
                f"{type(e).__name__}: {str(e)[:200]}"
            )
            return "error"


def _dict_to_paper(d: dict[str, Any]) -> NormalizedPaper:
    """Reverse the triage `_paper_as_dict` shape into NormalizedPaper.
    Tolerant — missing fields default to safe values."""
    published = d.get("published")
    pub_date = None
    if published:
        try:
            pub_date = date.fromisoformat(published)
        except (ValueError, TypeError):
            pub_date = None
    return NormalizedPaper(
        arxiv_id              = d.get("arxiv_id"),
        title                 = d.get("title", "") or "",
        abstract              = d.get("abstract", "") or "",
        published             = pub_date,
        authors               = tuple(d.get("authors", []) or []),
        categories            = tuple(d.get("categories", []) or []),
        citations             = int(d.get("citations") or 0),
        influential_citations = int(d.get("influential_citations") or 0),
        hn_points             = int(d.get("hn_points") or 0),
        hn_num_comments       = int(d.get("hn_num_comments") or 0),
        hf_upvotes            = int(d.get("hf_upvotes") or 0),
        sources               = frozenset(d.get("sources") or ()),
    )
