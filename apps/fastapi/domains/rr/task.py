"""Celery bridge for the Research Radar agent.

Queued from `POST /v1/rr/scan`; worker runs the full DeepAgents agent
end-to-end and persists the final digest. Phase progress streams over
Redis pub/sub for the SSE endpoint.

Same shape as `domains/dd/planner/task.py`: asyncio.run bridge + dict
return + try/except → status='failed' envelope. Phase events emitted
SYNCHRONOUSLY via `emit_event_sync` so they survive even when the
agent run is cancelled mid-await.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any
from uuid import UUID

from infra.celery import app

from .agent.graph import build_radar_agent
from .agent.tools.state import clear_scan_fs, fs_list, fs_read, init_scan_fs
from .agent.keys import (
    FS_FILE_DIGEST,
    FS_FILE_SYNTHESIS_REPORT,
    FS_FILE_TRIAGE_TOPN,
    fs_extraction_path,
)
from .entities import Extraction, Finding
from .runtime.events import emit_event_sync
from .service import (
    begin_scan,
    complete_scan,
    fail_scan,
    get_seen_ids,
    persist_scan_result,
)


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Task entry — sync wrapper around the async main. Architecture doc §2.6.2.
# --------------------------------------------------------------------------- #
@app.task(
    name           = "domains.rr.task.run_radar_scan",
    bind           = True,
    acks_late      = False,
    track_started  = True,
    # Architecture-doc §2.6.2 calls for soft_time_limit=1800s (30 min).
    # The agent's LLM cascade can chew that on a cold cache; +5 min hard
    # cap gives the cleanup paths time to fail loud rather than SIGKILL.
    soft_time_limit = 1800,
    time_limit      = 2100,
)
def run_radar_scan(
    self,
    scan_id: str,
    profile_id: str,
    topic: str,
    verticals: list[str] | None = None,
    top_n: int = 12,
) -> dict:
    """Run one Research Radar scan end-to-end.

    Args:
        scan_id:    UUID string from the API layer.
        profile_id: Interest-profile id (partitions radar_seen).
        topic:      Topical query (2-8 words) — fed into discovery
                    subagents' query field.
        verticals:  Vertical categories for signal_score.vertical_fit.
        top_n:      How many papers to deep-read after triage.

    Returns the same dict shape on success and failure (status field
    distinguishes); Celery serializes to JSON for the result backend.
    """
    verticals = verticals or []
    logger.info(
        f"[rr-task] run_radar_scan scan_id={scan_id} profile={profile_id!r} "
        f"topic={topic!r} verticals={verticals} top_n={top_n}"
    )
    try:
        return asyncio.run(
            _run_radar_scan_async(
                scan_id    = scan_id,
                profile_id = profile_id,
                topic      = topic,
                verticals  = verticals,
                top_n      = top_n,
            )
        )
    except Exception as e:
        # Outer catch — _run_radar_scan_async catches its own and emits
        # phase=error itself, so this only fires on infra failures (e.g.
        # asyncio.run setup, missing env vars). Still emit + return.
        logger.exception(f"[rr-task] run_radar_scan failed at outer scope: {e}")
        emit_event_sync(
            scan_id, "error",
            message = f"task-outer: {type(e).__name__}: {e}",
        )
        return {
            "scan_id":    scan_id,
            "profile_id": profile_id,
            "status":     "failed",
            "error":      f"{type(e).__name__}: {e}",
        }


# --------------------------------------------------------------------------- #
# Async core — the actual orchestration
# --------------------------------------------------------------------------- #
async def _run_radar_scan_async(
    scan_id: str,
    profile_id: str,
    topic: str,
    verticals: list[str],
    top_n: int,
) -> dict:
    """End-to-end async pipeline. Emits phase events as it goes."""
    scan_uuid = UUID(scan_id)

    # 1. Mark the scan running + init the per-scan virtual filesystem.
    # Persist the request shape so the Recent-scans dropdown can show
    # what each scan was looking for.
    await begin_scan(
        scan_uuid, profile_id,
        topic     = topic,
        verticals = verticals,
        top_n     = top_n,
    )
    init_scan_fs(scan_id)
    # Bind scan_id into the contextvar that the LLM counter callback
    # reads on every chat completion. Path-A 2026-06-16: per-phase
    # rotator-call counters get bumped in Redis as the agent runs,
    # then the drawer fetches them for KPI cards per pipeline node.
    from .runtime.llm_counter import set_scan as _set_llm_counter_scan
    _set_llm_counter_scan(scan_id)
    emit_event_sync(
        scan_id, "running",
        message = f"agent starting (topic={topic!r}, top_n={top_n})",
    )

    try:
        # 2. Build the agent + invoke. The orchestrator prompt parses the
        #    user message to extract scan_id + verticals + topic.
        user_message = (
            f"scan_id={scan_id} "
            f"profile_id={profile_id} "
            f"verticals={verticals} "
            f"topic='{topic}' "
            f"top_n={top_n}"
        )
        agent = await build_radar_agent()
        # Attach the LLM-counter callback via RunnableConfig so it
        # propagates into every nested call (orchestrator + subagents)
        # without needing model wrapping. The callback no-ops when no
        # scan_id is in the context — safe for the shared rotator chain.
        _llm_cb = getattr(agent, "_rr_llm_counter_cb", None)
        callbacks = [_llm_cb] if _llm_cb is not None else []
        await agent.ainvoke(
            {"messages": [{"role": "user", "content": user_message}]},
            config = {
                "configurable": {"thread_id": scan_id},
                "callbacks":     callbacks,
            },
        )

        # 3. Build the digest from fs (step-6 refactor 2026-06-12):
        #    The `report` LLM subagent is RETIRED. Assembly + persistence
        #    is now Python, not LLM-driven. The agent's job ends with
        #    fs/triage/top_n.json + fs/extractions/* + fs/synthesis/report.json
        #    on disk; THIS code reads them and assembles the final digest.
        #
        #    Auto-triage fallback stays: if the orchestrator's LLM ended
        #    without calling triage but ≥1 discovery did write, we run
        #    triage from Python here.
        if not fs_read(scan_id, FS_FILE_TRIAGE_TOPN):
            discovery_keys = fs_list(scan_id, prefix="discovery/")
            if discovery_keys:
                logger.warning(
                    f"[rr-task] orchestrator skipped triage_candidates; "
                    f"auto-running over {len(discovery_keys)} discovery file(s)"
                )
                from .agent.tools.triage import triage_candidates
                triage_candidates.invoke({
                    "scan_id": scan_id,
                    "profile_verticals": list(verticals),
                    "top_n": top_n,
                })

        digest = _build_digest_from_fs(scan_id)
        if not digest:
            raise RuntimeError(
                f"agent finished AND triage never wrote "
                f"{FS_FILE_TRIAGE_TOPN} AND no discovery tool stashed "
                f"anything. Pipeline collapsed at phase 1. Check "
                f"[fs-tool] discover_* INFO lines + LangFuse trace."
            )

        emit_event_sync(scan_id, "persisting", message="writing findings + digest")

        # 4. Diff against the profile's seen set so the digest can show
        #    'New since last scan'.
        seen_ids = await get_seen_ids(profile_id)
        items = digest.get("items") or []
        for item in items:
            aid = item.get("arxiv_id")
            item["is_new"] = bool(aid) and aid not in seen_ids

        # 5. Materialize Finding dataclasses for service.persist_scan_result.
        findings = [_item_to_finding(it) for it in items]
        await persist_scan_result(
            scan_uuid, profile_id,
            findings        = findings,
            digest_payload  = digest,
        )

        # 6. Close out the scan in Postgres.
        await complete_scan(
            scan_uuid,
            total_candidates = int(digest.get("total_candidates", len(items))),
            total_in_digest  = len(items),
        )

        summary = {
            "n_findings":          len(findings),
            "themes":              digest.get("themes", []),
            "degraded":            bool(digest.get("degraded")),
            "degradation_reasons": digest.get("degradation_reasons", []),
        }
        emit_event_sync(scan_id, "done", summary=summary)
        logger.info(
            f"[rr-task] run_radar_scan scan_id={scan_id} DONE "
            f"n_findings={len(findings)} degraded={summary['degraded']}"
        )
        return {
            "scan_id":    scan_id,
            "profile_id": profile_id,
            "status":     "done",
            **summary,
        }

    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.exception(f"[rr-task] run_radar_scan failed: {err}")
        emit_event_sync(scan_id, "error", message=err)
        try:
            await fail_scan(scan_uuid, err)
        except Exception as fe:
            logger.warning(f"[rr-task] fail_scan post-error also failed: {fe}")
        return {
            "scan_id":    scan_id,
            "profile_id": profile_id,
            "status":     "failed",
            "error":      err,
        }
    finally:
        # Clear the LLM-counter contextvar so subsequent non-RR work
        # in the worker doesn't attribute calls to this scan_id.
        try:
            _set_llm_counter_scan(None)
        except Exception:
            pass
        # Drop the scan's fs — module-level dict, so failing to clear
        # would leak memory across many scans.
        clear_scan_fs(scan_id)
        # Release THIS event loop's Neo4j + Qdrant async clients before
        # `asyncio.run()` tears the loop down. Without this, the clients'
        # connection pools leak sockets until __del__ runs at process GC.
        # The per-loop WeakKeyDictionary would still drop the cache entry,
        # but explicit close keeps the protocol layer tidy.
        from infra.neo4j   import close_neo4j
        from infra.qdrant  import close_qdrant
        try:
            await close_neo4j()
        except Exception as e:
            logger.warning(f"[rr-task] close_neo4j failed: {e}")
        try:
            await close_qdrant()
        except Exception as e:
            logger.warning(f"[rr-task] close_qdrant failed: {e}")


# --------------------------------------------------------------------------- #
# Helpers — dict → dataclass
# --------------------------------------------------------------------------- #
def _item_to_finding(item: dict[str, Any]) -> Finding:
    """Convert one digest item to a Finding dataclass for service.persist_*"""
    ex_dict = item.get("extraction")
    extraction = _extraction_from_dict(ex_dict) if isinstance(ex_dict, dict) else None
    return Finding(
        arxiv_id   = str(item.get("arxiv_id") or ""),
        rank       = int(item.get("rank") or 0),
        signal     = float(item.get("signal") or 0.0),
        title      = str(item.get("title") or ""),
        authors    = tuple(item.get("authors") or ()),
        summary    = str(item.get("summary") or ""),
        extraction = extraction,
        is_new     = bool(item.get("is_new", True)),
        themes     = tuple(item.get("themes") or ()),
        sources    = frozenset(item.get("sources") or ()),
    )


def _extraction_from_dict(d: dict[str, Any]) -> Extraction:
    return Extraction(
        arxiv_id     = str(d.get("arxiv_id") or ""),
        problem      = str(d.get("problem") or ""),
        method       = str(d.get("method") or ""),
        math         = str(d.get("math") or ""),
        how_to_build = str(d.get("how_to_build") or ""),
        money_angle  = str(d.get("money_angle") or ""),
        confidence   = float(d.get("confidence") or 0.0),
    )


def _build_digest_from_fs(scan_id: str) -> dict[str, Any] | None:
    """Assemble the final digest from what the agent left in fs.

    CANONICAL persistence path. Reads triage + extractions + synthesis
    from fs, builds Finding-shaped items, returns the digest dict. The
    LLM-written fs/digest.json (when present) is IGNORED — we always
    rebuild from upstream artifacts. This makes the report subagent's
    JSON-emission failures non-fatal (Fix 1+3 ship 2026-06-15).

    Degraded mode (2026-06-15): if synthesis is missing OR no extractions
    landed, builds the best digest possible and stamps `degraded=True` +
    `degradation_reasons=[...]`. UI surfaces a "partial data" indicator.
    Only returns None on TRUE phase-1 collapse: no top_n.json — meaning
    triage never ran, so we have no ranked paper list to render at all.
    """
    top_n = fs_read(scan_id, FS_FILE_TRIAGE_TOPN)
    if not isinstance(top_n, list) or not top_n:
        return None
    synth = fs_read(scan_id, FS_FILE_SYNTHESIS_REPORT) or {}
    extraction_paths = fs_list(scan_id, prefix="extractions/")
    extractions_by_id: dict[str, Any] = {}
    for p in extraction_paths:
        ex = fs_read(scan_id, p)
        if isinstance(ex, dict) and ex.get("arxiv_id"):
            extractions_by_id[ex["arxiv_id"]] = ex

    # Two sources for per-paper theme assignment, in priority order:
    #
    # 1. **Synthesis subagent's `per_paper_themes`** (2026-06-16 fix). The
    #    synthesis subagent now owns theme→paper assignment in a single
    #    structured field, validated against skill HARD RULES at the tool
    #    layer (strict subset of `themes`, max 2 per paper). This replaces
    #    the report subagent's per-item `themes` field, which had to be
    #    extracted from a JSON envelope the LLM repeatedly truncated to
    #    `{` (scan f52fb84a).
    #
    # 2. **Report subagent's digest.json `items[].themes`** (legacy path,
    #    kept as fallback). Still merged if synthesis didn't emit per-paper
    #    themes — useful when the synthesis-side rollout lags the report-
    #    side codebase, OR when running in "tools" mode (where the report
    #    subagent isn't even instantiated).
    #
    # Either source's empty value falls through to `[]`, which is the
    # correct answer per the skill (empty > the degenerate full-list copy).
    top_themes = synth.get("themes") or []
    top_themes_set = {t for t in top_themes if isinstance(t, str)}
    synth_ppt = synth.get("per_paper_themes") or {}
    if not isinstance(synth_ppt, dict):
        synth_ppt = {}

    llm_digest = fs_read(scan_id, FS_FILE_DIGEST) or {}
    llm_items_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(llm_digest, dict):
        for it in (llm_digest.get("items") or []):
            if isinstance(it, dict) and it.get("arxiv_id"):
                llm_items_by_id[it["arxiv_id"]] = it

    def _per_item_themes(aid: str) -> list[str]:
        """Lift this paper's themes from synthesis (preferred) or the
        report subagent's digest (fallback). Both sources go through the
        skill HARD RULES: subset of top-level themes; cap at 2."""
        # Source 1: synthesis subagent's per_paper_themes (preferred).
        synth_raw = synth_ppt.get(aid)
        if isinstance(synth_raw, list) and synth_raw:
            cleaned = [
                t for t in synth_raw
                if isinstance(t, str) and t in top_themes_set
            ]
            if cleaned:
                return cleaned[:2]
        # Source 2: report subagent's digest item themes (legacy / tools mode).
        llm_item = llm_items_by_id.get(aid) or {}
        raw = llm_item.get("themes") or []
        if not isinstance(raw, list):
            return []
        cleaned = [t for t in raw if isinstance(t, str) and t in top_themes_set]
        return cleaned[:2]

    items: list[dict[str, Any]] = []
    for i, paper in enumerate(top_n, start=1):
        if not isinstance(paper, dict):
            continue
        aid = paper.get("arxiv_id") or ""
        ex = extractions_by_id.get(aid)
        # One-line summary: the extraction's `problem` if present, else
        # the paper's title (better than a stale "(no extraction)" string).
        summary = ""
        if ex and ex.get("problem"):
            summary = ex["problem"][:240]
        if not summary:
            summary = paper.get("title") or "(untitled)"

        items.append({
            "arxiv_id":      aid,
            "rank":          i,
            "signal":        paper.get("signal", 0.0),
            # 2026-06-15: propagate the rerank cross-encoder logit from
            # triage's top_n.json. The field was added in agent/tools/triage.py
            # but `_build_digest_from_fs` was dropping it on the rebuild,
            # surfacing as `topical_logit: None` in every digest. Now
            # carried through so the drawer can render the relevance score.
            "topical_logit": paper.get("topical_logit"),
            "title":         paper.get("title") or "(untitled)",
            "authors":       paper.get("authors") or [],
            "summary":       summary,
            "themes":        _per_item_themes(aid),
            "sources":       paper.get("sources") or [],
            "extraction":    ex,
        })

    # Degradation diagnostics — surfaced on the digest envelope so the
    # UI can render a "partial data" badge and the operator knows which
    # phase fell short. Pipeline still completes; it just produces less.
    degradation_reasons: list[str] = []
    if not synth:
        degradation_reasons.append("synthesis_missing")
    if not extractions_by_id:
        degradation_reasons.append("no_extractions")
    elif len(extractions_by_id) < len(items):
        degradation_reasons.append(
            f"partial_extractions_{len(extractions_by_id)}_of_{len(items)}"
        )
    # Per-paper themes degradation: fires only when BOTH sources are
    # absent (synthesis didn't emit per_paper_themes AND the report
    # digest didn't emit items). With either, the per-item themes can
    # populate. When top_themes itself is empty (synthesis failed to
    # cluster), this check doesn't fire — there's no top-level set to
    # map paper to.
    if not synth_ppt and not llm_items_by_id and top_themes:
        degradation_reasons.append("no_llm_per_item_themes")

    # Per-item themes telemetry — tells us at a glance how many items
    # ended up with a non-empty themes list and which source supplied
    # them (synthesis vs report-digest fallback). Empty across the board
    # = a likely synthesis or report failure mode worth investigating.
    items_with_themes = sum(
        1 for it in items if it.get("themes")
    )
    themes_source_mix = {
        "synthesis_per_paper_themes":  len(synth_ppt),
        "llm_digest_items":            len(llm_items_by_id),
    }
    logger.info(
        f"[rr-task] build_digest_from_fs scan_id={scan_id} "
        f"items={len(items)} extractions_recovered={len(extractions_by_id)} "
        f"synthesis_themes={len(synth.get('themes') or [])} "
        f"items_with_per_item_themes={items_with_themes}/{len(items)} "
        f"theme_sources={themes_source_mix} "
        f"degraded={bool(degradation_reasons)} reasons={degradation_reasons}"
    )
    return {
        "scan_id":             scan_id,
        "summary":             synth.get("summary")
                               or f"Top {len(items)} papers from this radar scan",
        "themes":              synth.get("themes") or [],
        "items":               items,
        "total_candidates":    len(items),
        "degraded":            bool(degradation_reasons),
        "degradation_reasons": degradation_reasons,
    }
