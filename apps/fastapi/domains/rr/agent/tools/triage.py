"""Triage — pure orchestrator tool.

The deterministic Phase-2 node from the architecture doc. Reads the 4
discovery outputs from the scan's virtual fs, runs the domain pipeline
(normalize → dedup_by_arxiv_id → signal_score → top-N), writes the
ranked list back to fs.

No LLM — entirely deterministic. Wired into create_deep_agent's `tools=`
list so the orchestrator can invoke it after the 4 discovery subagents
return.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from langchain_core.tools import tool

from ..keys import (
    FS_FILE_TRIAGE_TOPN,
    fs_discovery_path,
)
from ...domain import (
    dedup_by_arxiv_id,
    diff_vs_seen,
    normalize_arxiv,
    normalize_hf,
    normalize_hn,
    normalize_s2,
    signal_score,
)
from ...entities import NormalizedPaper
from ...keys import (
    SOURCE_ARXIV,
    SOURCE_HF,
    SOURCE_HN,
    SOURCE_S2,
)
from ...params import WEIGHTS, DOMAIN_PARAMS
from .state import fs_list, fs_read, fs_write


logger = logging.getLogger(__name__)


# Source → normalizer mapping. Looked up at runtime so we can stay tolerant
# to a missing discovery output (e.g. the hn subagent crashed mid-scan).
_NORMALIZER_BY_SOURCE = {
    SOURCE_ARXIV: normalize_arxiv,
    SOURCE_S2:    normalize_s2,
    SOURCE_HF:    normalize_hf,
    SOURCE_HN:    normalize_hn,
}


@tool
def triage_candidates(
    scan_id: str,
    profile_verticals: list[str] | None = None,
    top_n: int = 12,
) -> str:
    """Rank discovery candidates by signal_score; write top-N to fs/triage.

    Call this AFTER all 4 discovery subagents have returned (their results
    are stashed in fs under `discovery/<source>.json` by their stash tool
    calls).

    Args:
        scan_id: Identifier for this radar scan (provided in your initial
            user message — pass it through).
        profile_verticals: Profile's vertical categories (e.g. ['cs.LG',
            'cs.AI', 'q-fin.PR']). Pass an empty list if the user didn't
            specify any.
        top_n: How many papers to keep for deep_read. Defaults to 12;
            range 8-20 is reasonable.

    Returns:
        A short summary including the count of candidates examined, the
        count after dedup, and the path written.
    """
    # Read each source's stashed discovery output. Missing → empty list
    # (one failed source shouldn't block triage).
    candidates: list[NormalizedPaper] = []
    per_source_counts: dict[str, int] = {}
    for source, normalizer in _NORMALIZER_BY_SOURCE.items():
        path = fs_discovery_path(source)
        raw = fs_read(scan_id, path)
        if raw is None:
            per_source_counts[source] = 0
            continue
        # Tolerate string JSON (legacy path) or pre-parsed list
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"[triage] {path} contained invalid JSON; skipping")
                per_source_counts[source] = 0
                continue
        if not isinstance(raw, list):
            logger.warning(f"[triage] {path} was {type(raw).__name__}, expected list")
            per_source_counts[source] = 0
            continue
        normalized = [normalizer(d) for d in raw if isinstance(d, dict)]
        candidates.extend(normalized)
        per_source_counts[source] = len(normalized)

    if not candidates:
        msg = f"[triage] no candidates from any source ({per_source_counts})"
        logger.warning(msg)
        fs_write(scan_id, FS_FILE_TRIAGE_TOPN, [])
        return msg

    # Cross-source dedup — the architectural payoff (architecture doc §4).
    deduped = dedup_by_arxiv_id(candidates)

    # Score each — pure function. embedding=None means relevance term = 0;
    # vertical_fit + recency + buzz + velocity + influential_ratio drive
    # the ranking until the embedding pipeline lands (step 3+ embed_via_router).
    now = date.today()
    verticals = tuple(profile_verticals or ())
    scored = [
        (p, signal_score(
            p,
            now               = now,
            profile_embedding = None,
            profile_verticals = verticals,
            weights           = WEIGHTS,
            domain_params     = DOMAIN_PARAMS,
        ))
        for p in deduped
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[: max(1, int(top_n))]

    # Serialize top-N as a list of dicts for downstream subagents to read.
    payload = [_paper_as_dict(p, score=s) for p, s in top]
    fs_write(scan_id, FS_FILE_TRIAGE_TOPN, payload)

    # Surface the top arxiv_ids in the return string so the orchestrator's
    # LLM knows which IDs to dispatch deep_read for in Phase 3 without
    # having to read fs separately. Subagents that need full paper data
    # still load it via read_top_n_papers.
    top_arxiv_ids = [p.arxiv_id for p, _ in top if p.arxiv_id]
    msg = (
        f"[triage] in={sum(per_source_counts.values())} "
        f"deduped={len(deduped)} top_n={len(top)} "
        f"per_source={per_source_counts} "
        f"top_score={top[0][1]:.4f} bottom_score={top[-1][1]:.4f} "
        f"top_arxiv_ids={top_arxiv_ids}"
    )
    logger.info(msg)
    return msg


def _paper_as_dict(p: NormalizedPaper, *, score: float) -> dict[str, Any]:
    """Materialize a NormalizedPaper as a JSON-safe dict for fs storage."""
    return {
        "arxiv_id":              p.arxiv_id,
        "title":                 p.title,
        "abstract":              p.abstract,
        "published":             p.published.isoformat() if p.published else None,
        "authors":               list(p.authors),
        "categories":            list(p.categories),
        "citations":             p.citations,
        "influential_citations": p.influential_citations,
        "hn_points":             p.hn_points,
        "hn_num_comments":       p.hn_num_comments,
        "hf_upvotes":            p.hf_upvotes,
        "sources":               sorted(p.sources),
        "signal":                float(score),
    }
