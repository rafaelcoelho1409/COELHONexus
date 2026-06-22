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
from infra.langfuse import (
    set_current_span_langfuse_io,
    set_current_span_langfuse_observation_metadata,
    set_current_span_langfuse_trace_metadata,
)
from infra.otel import get_tracer

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
from .runtime.observability import record_scan_run
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
    """Span + metrics wrapper around the RR scan orchestration."""
    t0 = asyncio.get_running_loop().time()
    with get_tracer().start_as_current_span(
        "rr.scan.run",
        attributes = {
            "rr.scan_id":        scan_id,
            "rr.profile_id":     profile_id,
            "rr.topic":          topic[:200],
            "rr.vertical_count": len(verticals),
            "rr.top_n":          top_n,
        },
    ):
        set_current_span_langfuse_io(input_data = {
            "topic": topic,
            "verticals": list(verticals or []),
            "top_n": top_n,
            "scan_id": scan_id,
            "profile_id": profile_id,
        })
        set_current_span_langfuse_trace_metadata({
            "pipeline": "rr_scan",
            "scan_id": scan_id,
            "profile_id": profile_id,
            "vertical_count": len(verticals),
            "top_n": top_n,
        })
        set_current_span_langfuse_observation_metadata({
            "topic": topic[:200],
            "vertical_count": len(verticals),
        })
        try:
            result = await _run_radar_scan_async_inner(
                scan_id = scan_id,
                profile_id = profile_id,
                topic = topic,
                verticals = verticals,
                top_n = top_n,
            )
        except Exception as e:
            set_current_span_langfuse_io(output_data = {
                "status": "failed",
                "error": f"{type(e).__name__}: {e}",
                "n_findings": 0,
                "total_candidates": 0,
                "themes": [],
                "degraded": True,
            })
            raise
        set_current_span_langfuse_io(output_data = {
            "status": result.get("status", "unknown"),
            "n_findings": int(result.get("n_findings", 0) or 0),
            "total_candidates": int(
                result.get("total_candidates", result.get("n_findings", 0)) or 0
            ),
            "themes": list(result.get("themes") or [])[:10],
            "degraded": bool(result.get("degraded", result.get("status") != "done")),
            "degradation_reasons": list(result.get("degradation_reasons") or [])[:10],
            "error": result.get("error"),
        })
    record_scan_run(
        degraded = bool(result.get("degraded", result.get("status") != "done")),
        outcome = str(result.get("status") or "unknown"),
        duration_s = max(asyncio.get_running_loop().time() - t0, 0.0),
        findings = int(result.get("n_findings", 0) or 0),
        candidates = int(result.get("total_candidates", result.get("n_findings", 0)) or 0),
        theme_count = len(result.get("themes") or []),
    )
    return result


async def _run_radar_scan_async_inner(
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
        from infra.langfuse.sessions import session as _lf_session
        callbacks = [c for c in (_llm_cb,) if c is not None]
        # Stamp baggage so every span (including raw rotator httpx) inherits
        # session_id / user_id / digest_id. session() is sync; OTel context
        # propagates across await boundaries via contextvars.
        with _lf_session(
            "rr",
            session_id = scan_id,
            user_id    = profile_id,
            digest_id  = scan_id,
        ):
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

        # 2026-06-16: missing-extractions inline backfill. After the agent
        # returns, check (top_n - extractions on disk). For each missing
        # arxiv_id (up to BACKFILL_MAX, default 3), fire one bandit-routed
        # LLM call inline to produce the extraction directly — bypassing
        # the orchestrator and the deep_read subagent. This recovers most
        # of what was previously a "partial_extractions_X_of_Y" degradation
        # (scan fd9ad127 dropped 1/8 because the phase enforcer exhausted
        # before the orchestrator finished the last deep_read). Skipping
        # when ≥4 are missing — that's a deeper infra issue (rotator down,
        # all arms cooled, etc.) and inline retry won't help.
        try:
            await _backfill_missing_extractions(scan_id)
        except Exception as e:
            logger.warning(
                f"[rr-task] backfill_missing_extractions threw "
                f"{type(e).__name__}: {e}"
            )

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
            "total_candidates": int(digest.get("total_candidates", len(items))),
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
        # 2026-06-17: snapshot LLM counters from Redis → Postgres
        # (radar_scans.llm_counters JSONB) so the per-scan telemetry
        # (drawer KPIs + totals strip) survives Redis TTL expiry. Runs
        # on every exit path (success / failure / cancellation). DELETE
        # on the scan row removes the counters atomically — no separate
        # cleanup in the trash-button flow.
        try:
            from .runtime.llm_counter import snapshot_to_postgres
            await snapshot_to_postgres(scan_id)
        except Exception as e:
            logger.warning(
                f"[rr-task] llm-counter snapshot failed scan_id={scan_id}: "
                f"{type(e).__name__}: {e}"
            )
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
    # Fix #5 (2026-06-16): sparse-themes degradation. Scan 5 had
    # items_with_per_item_themes=2/12 (17%) but `degraded=False` — the
    # checks above only fire when sources are completely missing, not
    # when synthesis ran but the mapping is mostly empty. Catch the
    # "right count, wrong quality" failure mode: if <50% of items got
    # a non-empty themes list AND we have ≥4 items (avoid noise on
    # tiny scans), flag it. Skip when top_themes is empty (already
    # covered by the synthesis_missing / no_llm_per_item_themes flags
    # above — would double-flag).
    if (
        top_themes
        and len(items) >= 4
        and items_with_themes < 0.5 * len(items)
    ):
        degradation_reasons.append(
            f"sparse_per_item_themes_{items_with_themes}_of_{len(items)}"
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


# --------------------------------------------------------------------------- #
# Missing-extractions inline backfill — Fix #2 (2026-06-16)
# --------------------------------------------------------------------------- #
# When the orchestrator drops a deep_read (phase enforcer exhausted, subagent
# crashed, etc.), we don't want the scan to degrade silently. After the agent
# returns, this helper checks `top_n.json - fs/extractions/*` and fires one
# bandit-routed LLM call per missing arxiv_id (capped) to produce the
# extraction directly — bypassing both the orchestrator and the deep_read
# subagent. The extraction lands in the same fs path the deep_read subagent
# would have written, so `_build_digest_from_fs` sees a complete set and
# `degraded=False`.
#
# Quality notes:
#   - Same model the deep_read subagent uses (`build_rr_strong_chain_bandit`)
#     → same bandit pool, same FGTS-VA selection, same cascade behavior
#   - Same system prompt (paper_extraction skill + DEEP_READ_SYSTEM_PROMPT)
#     → no quality drift vs subagent path
#   - Backfill calls go through the LLM-counter as `phase=deep_read` so
#     they're visible in the drawer just like subagent-driven calls
#   - Cap of 3 backfills per scan — beyond that, infra is likely wedged
#     and inline retry just burns time + tokens without recovery
BACKFILL_MAX = 3


async def _backfill_missing_extractions(scan_id: str) -> None:
    """Inline-recover any top_n arxiv_id whose extraction never landed.
    No-op when extractions are complete OR more than BACKFILL_MAX are
    missing. Best-effort — failures log and proceed (downstream digest
    assembly will mark them as `partial_extractions_X_of_Y` degradation
    just like before)."""
    top_n_raw = fs_read(scan_id, FS_FILE_TRIAGE_TOPN)
    if not isinstance(top_n_raw, list) or not top_n_raw:
        return
    expected_ids = {
        p.get("arxiv_id") for p in top_n_raw
        if isinstance(p, dict) and p.get("arxiv_id")
    }
    expected_ids.discard(None)
    if not expected_ids:
        return
    # What's already on disk.
    extracted_paths = fs_list(scan_id, prefix="extractions/")
    extracted_ids: set[str] = set()
    for p in extracted_paths:
        rec = fs_read(scan_id, p)
        if isinstance(rec, dict) and rec.get("arxiv_id"):
            extracted_ids.add(rec["arxiv_id"])
    missing_ids = expected_ids - extracted_ids
    if not missing_ids:
        return
    if len(missing_ids) > BACKFILL_MAX:
        logger.warning(
            f"[rr-task] backfill skipped scan_id={scan_id} "
            f"missing={len(missing_ids)} > BACKFILL_MAX={BACKFILL_MAX} "
            f"(likely infra issue — letting digest degrade naturally)"
        )
        return
    logger.info(
        f"[rr-task] backfill firing scan_id={scan_id} "
        f"missing={sorted(missing_ids)}"
    )
    # Index paper data by arxiv_id so the backfill prompt has title+abstract.
    paper_by_id = {
        p.get("arxiv_id"): p
        for p in top_n_raw
        if isinstance(p, dict) and p.get("arxiv_id")
    }
    # Lazy imports — only what this function uses; `_backfill_one` re-imports
    # the deep_read-specific prompt + skill + tool itself.
    from domains.llm.rotator.chain.service import build_rr_strong_chain_bandit
    from .runtime.llm_counter import set_phase as _set_llm_phase

    chain = build_rr_strong_chain_bandit()
    # Phase attribution: backfill calls bucket under deep_read so the
    # drawer KPIs show them as part of the deep_read activity.
    try: _set_llm_phase("deep_read")
    except Exception: pass

    backfilled = 0
    for arxiv_id in sorted(missing_ids):
        paper = paper_by_id.get(arxiv_id)
        if not isinstance(paper, dict):
            continue
        try:
            await _backfill_one(scan_id, arxiv_id, paper, chain)
            backfilled += 1
        except Exception as e:
            logger.warning(
                f"[rr-task] backfill failed for {arxiv_id}: "
                f"{type(e).__name__}: {e}"
            )
    logger.info(
        f"[rr-task] backfill done scan_id={scan_id} "
        f"recovered={backfilled}/{len(missing_ids)}"
    )


async def _backfill_one(
    scan_id: str,
    arxiv_id: str,
    paper: dict[str, Any],
    chain: Any,
) -> None:
    """Run one inline deep_read against the bandit chain + write the
    extraction. Raises on any failure (caller catches)."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from .agent.skills import SKILL_PAPER_EXTRACTION
    from .agent.tools.fs_tools import write_extraction

    title    = (paper.get("title")    or "").strip()
    abstract = (paper.get("abstract") or "").strip()
    if not abstract:
        raise RuntimeError(f"no abstract on disk for {arxiv_id}")

    # Same composition the deep_read subagent uses for its system prompt:
    # paper_extraction skill (the 5-field rubric + failure-mode warnings)
    # + DEEP_READ_SYSTEM_PROMPT (the tool-flow glue). For inline we strip
    # the "call read_top_n_papers / write_extraction" steps because we
    # already have the paper + we'll persist via Python below — just keep
    # the extraction guidance.
    system_prompt = (
        "=== SKILL: paper_extraction ===\n\n"
        f"{SKILL_PAPER_EXTRACTION}\n\n"
        "=== ROLE ===\n\n"
        "You are extracting structured fields from ONE paper. Return your "
        "answer as a SINGLE JSON object with exactly these keys: "
        "`problem`, `method`, `math`, `how_to_build`, `money_angle`, "
        "`confidence`. `confidence` is a float in [0, 1]. The other fields "
        "are strings. Output ONLY the JSON object — no prose, no markdown "
        "fences."
    )
    user_msg = (
        f"arxiv_id: {arxiv_id}\n"
        f"title: {title}\n\n"
        f"abstract:\n{abstract}\n"
    )
    response = await chain.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_msg),
    ])
    raw = (getattr(response, "content", None) or "").strip()
    if not raw:
        raise RuntimeError("empty content from rotator")
    # Strip code fences defensively (some arms wrap JSON in ```json ... ```).
    if raw.startswith("```"):
        lines = raw.splitlines()
        # Drop the opening fence (and optional language tag) + the closing fence
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    import json as _json
    try:
        data = _json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"json parse failed: {e}; head={raw[:120]!r}")
    if not isinstance(data, dict):
        raise RuntimeError(f"non-dict json: type={type(data).__name__}")
    # Defensive defaults — write_extraction tool will clamp / validate.
    payload = {
        "scan_id":      scan_id,
        "arxiv_id":     arxiv_id,
        "problem":      str(data.get("problem")      or "").strip(),
        "method":       str(data.get("method")       or "").strip(),
        "math":         str(data.get("math")         or "").strip(),
        "how_to_build": str(data.get("how_to_build") or "").strip(),
        "money_angle":  str(data.get("money_angle")  or "").strip(),
        "confidence":   float(data.get("confidence") or 0.5),
    }
    # Reuse the @tool's persistence path so MinIO mirror + cache write +
    # retry detection + SSE emit all fire exactly as for a subagent-driven
    # deep_read. The retry counter will tick if a backfill overwrites an
    # extraction that arrived after our read — harmless tiny race.
    write_extraction.invoke(payload)
    logger.info(
        f"[rr-task] backfill wrote extraction arxiv_id={arxiv_id} "
        f"confidence={payload['confidence']:.2f}"
    )
