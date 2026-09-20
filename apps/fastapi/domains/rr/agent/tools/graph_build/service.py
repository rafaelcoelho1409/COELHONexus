"""Graph-build — pure I/O orchestrator tool. Imperative Shell.

The deterministic Phase-4 node from the architecture doc. Reads the
ranked top-N from fs, embeds each paper's abstract via the Settings-
page-configured embedding endpoint, and persists to Neo4j + Qdrant via
`domains.rr.service.persist_paper`.

No chat LLM here — embeddings are a separate, independently-configured
connection. Wired into create_deep_agent's `tools=` list so the
orchestrator can invoke it after deep_read finishes.

2026-09-17: was calling `domains.llm.rotator.chain.service.
embed_via_router_async` — that function is NOT the Settings-page-
configured embedding endpoint despite the name; it's local in-process
FastEmbed ONNX (384d), with a direct-to-NVIDIA-API fallback as a last
resort, bypassing the Settings page entirely. `radar_papers`'
collection has always been sized for 2048d (matching NIM's real
embedding model), so every upsert since that local-FastEmbed fallback
became the effective path was silently rejected by Qdrant with a 400 —
100% failure, confirmed live (persisted=0/8 on the last real scan).
`domains.llm.embeddings.embed_texts_async` is the genuine Settings-
page-configured path (`api/v1/llm/settings/router.py`'s `/embedding`
routes) — confirmed live to resolve to `nim/nvidia/nemotron-3-embed-1b`
at 2048d, exactly matching the collection, so no resize/migration is
needed, just this call-site fix."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.tools import tool

import domains
from domains.llm.embeddings.service import embed_texts_async

from . import domain
from .. import state as tools_state
from ... import keys
from .... import service as rr_service


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
    top_n = tools_state.fs_read(scan_id, keys.FS_FILE_TRIAGE_TOPN)
    if isinstance(top_n, list) and not top_n:
        # A genuinely empty top_n (triage ran, found 0 candidates) is
        # FINAL — unlike the `top_n is None` branch below, retrying this
        # call can never produce a different result. Observed live (scan
        # b4643c7b, 2026-09-20): without an explicit "don't call this
        # again", the orchestrator called graph_build_papers 3 times on
        # an empty top_n before giving up.
        msg = (
            f"[graph_build] scan_id={scan_id}: triage found 0 candidates "
            f"({keys.FS_FILE_TRIAGE_TOPN} = []) — this is FINAL, there is "
            f"nothing to persist. Do NOT call graph_build_papers again for "
            f"this scan; emit your final ScanComplete response instead."
        )
        logger.warning(msg)
        return msg
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
            f"{keys.FS_FILE_TRIAGE_TOPN} (got type={observed} value={observed_repr}) "
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
    # Completion marker — lets PhaseEnforcerMiddleware.awrap_tool_call
    # hard-block a redundant redispatch of this scan's graph_build the
    # same way an already-resolved discovery source or synthesis report
    # is blocked (Neo4j/Qdrant upserts are idempotent so a repeat call
    # isn't WRONG, just wasted rotator + DB round-trips).
    tools_state.fs_write(
        scan_id, keys.FS_FILE_GRAPH_BUILD_DONE,
        {"persisted": persisted, "skipped": skipped, "errors": errors},
    )
    # Phase contextvar for LLM-counter attribution (Path A).
    try:
        domains.rr.runtime.llm_counter.service.set_phase("graph_build")
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
    paper = domain.dict_to_paper(item)
    embedding: list[float] | None = None
    async with sem:
        try:
            if abstract:
                vecs, _model = await embed_texts_async([abstract])
                embedding = vecs[0] if vecs else None
            await rr_service.persist_paper(paper, embedding=embedding, signal=item.get("signal"))
            return "ok"
        except Exception as e:
            logger.warning(
                f"[graph_build] persist failed for {arxiv_id}: "
                f"{type(e).__name__}: {str(e)[:200]}"
            )
            return "error"
