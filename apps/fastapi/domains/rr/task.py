"""Celery bridge for the Research Radar agent.

Queued from `POST /v1/rr/scan`; worker runs the full DeepAgents agent
end-to-end and persists the final digest. Phase progress streams over
Redis pub/sub for the SSE endpoint.

Same shape as `domains/dd/planner/task.py`: asyncio.run bridge + dict
return + try/except → status='failed' envelope. Phase events emitted
SYNCHRONOUSLY via `runtime.service.emit_event_sync` so they survive even when the
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
from . import agent, entities, runtime, service, stores


logger = logging.getLogger(__name__)


@app.task(
    name           = "domains.rr.task.run_radar_scan",
    bind           = True,
    acks_late      = False,
    track_started  = True,
    # 2026-09-17: soft_time_limit=1800/time_limit=2100 removed — same
    # bug DD's Planner hit and fixed on 2026-09-09 (see that task's
    # comment). Confirmed live: scan c01bf761 was killed by
    # SoftTimeLimitExceeded at exactly 1800.03s while STILL making real
    # progress (graph_build had just persisted 8/8 papers moments
    # before; synthesis was mid-retry-loop, not hung) — a prior scan on
    # the identical topic/top_n had already finished healthily in
    # 1557s, 87% of this same budget, so the margin was never safe
    # against normal NIM latency variance. Celery's signal-based
    # timeout can't distinguish "hung" from "genuinely slow"; RR
    # already has a real cooperative-cancel path for a truly-stuck scan
    # (`service.cancel_scan` → `celery_app.control.revoke(...,
    # terminate=True)`, wired to the UI's cancel button) — no blanket
    # wall-clock cap needed on top of it.
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
        err = f"{type(e).__name__}: {e}"
        logger.exception(f"[rr-task] run_radar_scan failed at outer scope: {e}")
        runtime.service.emit_event_sync(
            scan_id, "error",
            message = f"task-outer: {err}",
        )
        # 2026-09-17: this outer handler was missing the `service.fail_scan`
        # call the INNER handler (`_run_radar_scan_async`'s own
        # try/except, above) already has — confirmed live: scan
        # c01bf761 hit SoftTimeLimitExceeded here specifically (the
        # signal escaping the inner try/except, landing in this outer
        # one instead — visible from the "at outer scope" log line),
        # emitted the SSE error event, but the `radar_scans` Postgres
        # row was NEVER marked failed. It stayed `status='running',
        # finished_at=NULL` forever — anything computing elapsed time
        # from `started_at` with no `finished_at` shows an ever-
        # growing duration (this row was already ~4h40m "running" by
        # the time it was found, not a real 4-hour scan).
        try:
            from uuid import UUID
            asyncio.run(service.fail_scan(UUID(scan_id), err))
        except Exception as fe:
            logger.warning(f"[rr-task] outer-scope service.fail_scan also failed: {fe}")
        return {
            "scan_id":    scan_id,
            "profile_id": profile_id,
            "status":     "failed",
            "error":      err,
        }


async def _run_radar_scan_async(
    scan_id: str,
    profile_id: str,
    topic: str,
    verticals: list[str],
    top_n: int,
) -> dict:
    """Span + metrics wrapper around the RR scan orchestration."""
    from infra.langfuse.sessions import session as _lf_session
    t0 = asyncio.get_running_loop().time()
    with _lf_session(
        "rr",
        session_id = scan_id,
        user_id    = profile_id,
        digest_id  = scan_id,
    ):
        with get_tracer().start_as_current_span(
            "rr.scan.run",
            attributes = {
                "coelho.langfuse.keep": True,
                "coelho.langfuse.kind": "workflow_root",
                "langfuse.trace.name": "rr.scan.run",
                "rr.scan_id":        scan_id,
                "rr.profile_id":     profile_id,
                "rr.topic":          topic[:200],
                "rr.vertical_count": len(verticals),
                "rr.top_n":          top_n,
                "langfuse.observation.metadata.workflow": "rr_scan",
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
    runtime.metrics.record_scan_run(
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

    await service.begin_scan(
        scan_uuid, profile_id,
        topic     = topic,
        verticals = verticals,
        top_n     = top_n,
    )
    agent.tools.state.init_scan_fs(scan_id)
    runtime.llm_counter.service.set_scan(scan_id)
    runtime.service.emit_event_sync(
        scan_id, "running",
        message = f"agent starting (topic={topic!r}, top_n={top_n})",
    )

    try:
        user_message = (
            f"scan_id={scan_id} "
            f"profile_id={profile_id} "
            f"verticals={verticals} "
            f"topic='{topic}' "
            f"top_n={top_n}"
        )
        radar_agent = await build_radar_agent()
        _llm_cb = getattr(radar_agent, "_rr_llm_counter_cb", None)
        callbacks = [c for c in (_llm_cb,) if c is not None]
        await radar_agent.ainvoke(
            {"messages": [{"role": "user", "content": user_message}]},
            config = {
                "configurable": {"thread_id": scan_id},
                "callbacks":     callbacks,
            },
        )
        if _mw := getattr(radar_agent, "_rr_phase_middleware", None):
            _mw.finalize_scan(scan_id)

        # Auto-triage fallback: if the orchestrator skipped triage but discovery wrote, run triage from Python.
        if not agent.tools.state.fs_read(scan_id, agent.keys.FS_FILE_TRIAGE_TOPN):
            discovery_keys = agent.tools.state.fs_list(scan_id, prefix="discovery/")
            if discovery_keys:
                logger.warning(
                    f"[rr-task] orchestrator skipped triage_candidates; "
                    f"auto-running over {len(discovery_keys)} discovery file(s)"
                )
                await agent.tools.triage.service.triage_candidates.ainvoke({
                    "scan_id": scan_id,
                    "topic": topic,
                    "profile_verticals": list(verticals),
                    "top_n": top_n,
                })

        with get_tracer().start_as_current_span(
            "rr.node.backfill",
            attributes={"coelho.langfuse.keep": True, "rr.scan_id": scan_id},
        ):
            try:
                await _backfill_missing_extractions(scan_id)
            except Exception as e:
                logger.warning(
                    f"[rr-task] backfill_missing_extractions threw "
                    f"{type(e).__name__}: {e}"
                )

        with get_tracer().start_as_current_span(
            "rr.node.digest_assemble",
            attributes={"coelho.langfuse.keep": True, "rr.scan_id": scan_id},
        ):
            digest = _build_digest_from_fs(scan_id)
            if not digest:
                raise RuntimeError(
                    f"agent finished but {agent.keys.FS_FILE_TRIAGE_TOPN} was "
                    f"never written — no discovery tool stashed ANYTHING for "
                    f"this scan (a zero-candidate run still writes an empty "
                    f"top_n.json, so this means discovery itself never ran). "
                    f"Pipeline collapsed at phase 1. Check [fs-tool] discover_* "
                    f"INFO lines + LangFuse trace."
                )

            runtime.service.emit_event_sync(scan_id, "persisting", message="writing findings + digest")

            seen_ids = await service.get_seen_ids(profile_id)
            items = digest.get("items") or []
            for item in items:
                aid = item.get("arxiv_id")
                item["is_new"] = bool(aid) and aid not in seen_ids

            findings = [_item_to_finding(it) for it in items]
            await service.persist_scan_result(
                scan_uuid, profile_id,
                findings       = findings,
                digest_payload = digest,
            )
            await service.complete_scan(
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
        runtime.service.emit_event_sync(scan_id, "done", summary=summary)
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
        runtime.service.emit_event_sync(scan_id, "error", message=err)
        try:
            await service.fail_scan(scan_uuid, err)
        except Exception as fe:
            logger.warning(f"[rr-task] service.fail_scan post-error also failed: {fe}")
        return {
            "scan_id":    scan_id,
            "profile_id": profile_id,
            "status":     "failed",
            "error":      err,
        }
    finally:
        # Snapshot counters to Postgres so they survive Redis TTL expiry.
        try:
            await runtime.llm_counter.service.snapshot_to_postgres(scan_id)
        except Exception as e:
            logger.warning(
                f"[rr-task] llm-counter snapshot failed scan_id={scan_id}: "
                f"{type(e).__name__}: {e}"
            )
        try:
            runtime.llm_counter.service.set_scan(None)
        except Exception:
            pass
        agent.tools.state.clear_scan_fs(scan_id)
        # Explicit close: asyncio.run() tears the loop down before __del__ runs, leaking sockets otherwise.
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


def _item_to_finding(item: dict[str, Any]) -> entities.Finding:
    """Convert one digest item to a entities.Finding dataclass for service.persist_*"""
    ex_dict = item.get("extraction")
    extraction = _extraction_from_dict(ex_dict) if isinstance(ex_dict, dict) else None
    return entities.Finding(
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


def _extraction_from_dict(d: dict[str, Any]) -> entities.Extraction:
    return entities.Extraction(
        arxiv_id     = str(d.get("arxiv_id") or ""),
        problem      = str(d.get("problem") or ""),
        method       = str(d.get("method") or ""),
        math         = str(d.get("math") or ""),
        how_to_build = str(d.get("how_to_build") or ""),
        money_angle  = str(d.get("money_angle") or ""),
        confidence   = float(d.get("confidence") or 0.0),
    )


def _build_digest_from_fs(scan_id: str) -> dict[str, Any] | None:
    """Assemble the digest from fs artifacts. Returns None ONLY on a true
    phase-1 collapse — `top_n.json` was never written at all. An empty
    `top_n.json` (`[]`) is a legitimate "0 candidates this scan" result
    (triage always writes it, even on a zero-candidate run — see
    triage/service.py) and produces a valid, non-degraded, 0-item digest
    below rather than being treated the same as a collapse.

    Always rebuilds from triage+extractions+synthesis — never trusts the LLM-written digest.json."""
    top_n = agent.tools.state.fs_read(scan_id, agent.keys.FS_FILE_TRIAGE_TOPN)
    if top_n is None or not isinstance(top_n, list):
        return None
    synth = agent.tools.state.fs_read(scan_id, agent.keys.FS_FILE_SYNTHESIS_REPORT) or {}
    extraction_paths = agent.tools.state.fs_list(scan_id, prefix="extractions/")
    extractions_by_id: dict[str, Any] = {}
    for p in extraction_paths:
        ex = agent.tools.state.fs_read(scan_id, p)
        if isinstance(ex, dict) and ex.get("arxiv_id"):
            extractions_by_id[ex["arxiv_id"]] = ex

    # Per-paper themes: synthesis.per_paper_themes preferred; digest.json items[].themes as fallback.
    top_themes = synth.get("themes") or []
    top_themes_set = {t for t in top_themes if isinstance(t, str)}
    synth_ppt = synth.get("per_paper_themes") or {}
    if not isinstance(synth_ppt, dict):
        synth_ppt = {}

    llm_digest = agent.tools.state.fs_read(scan_id, agent.keys.FS_FILE_DIGEST) or {}
    llm_items_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(llm_digest, dict):
        for it in (llm_digest.get("items") or []):
            if isinstance(it, dict) and it.get("arxiv_id"):
                llm_items_by_id[it["arxiv_id"]] = it

    def _per_item_themes(aid: str) -> list[str]:
        """Synthesis per_paper_themes preferred; digest items[].themes fallback. Both capped at 2."""
        synth_raw = synth_ppt.get(aid)
        if isinstance(synth_raw, list) and synth_raw:
            cleaned = [
                t for t in synth_raw
                if isinstance(t, str) and t in top_themes_set
            ]
            if cleaned:
                return cleaned[:2]
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
            "topical_logit": paper.get("topical_logit"),
            "title":         paper.get("title") or "(untitled)",
            "authors":       paper.get("authors") or [],
            "summary":       summary,
            "themes":        _per_item_themes(aid),
            "sources":       paper.get("sources") or [],
            "extraction":    ex,
        })

    degradation_reasons: list[str] = []
    if items and not synth:
        degradation_reasons.append("synthesis_missing")
    if items and not extractions_by_id:
        degradation_reasons.append("no_extractions")
    elif len(extractions_by_id) < len(items):
        degradation_reasons.append(
            f"partial_extractions_{len(extractions_by_id)}_of_{len(items)}"
        )
    if items and not synth_ppt and not llm_items_by_id and top_themes:
        degradation_reasons.append("no_llm_per_item_themes")

    items_with_themes = sum(1 for it in items if it.get("themes"))
    # Sparse-themes degradation: <50% with themes on ≥4-item scans = mapping mostly empty.
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
        "summary":             synth.get("summary") or (
                                   "No new papers matched this scan's topic/verticals "
                                   "across any source — try again later or broaden the "
                                   "search." if not items
                                   else f"Top {len(items)} papers from this radar scan"
                               ),
        "themes":              synth.get("themes") or [],
        "items":               items,
        "total_candidates":    len(items),
        "degraded":            bool(degradation_reasons),
        "degradation_reasons": degradation_reasons,
    }


# Inline backfill: recover extractions dropped by the orchestrator (phase enforcer exhausted, etc.).
# Capped at 3 — beyond that, infra is likely wedged and retrying won't help.
BACKFILL_MAX = 3

# Per-call bound for one backfill extraction (single abstract → 6-field
# JSON — much lighter than deep_read's full-paper-text shape). 2 attempts:
# a transient NIM timeout/connection blip gets one retry via
# `resilient_ainvoke`, same accelerator YCS Ask/Query use.
_BACKFILL_CALL_TIMEOUT_S = 90.0


async def _backfill_missing_extractions(scan_id: str) -> None:
    """Recover extractions missing from fs up to BACKFILL_MAX. No-op when complete or gap > cap."""
    top_n_raw = agent.tools.state.fs_read(scan_id, agent.keys.FS_FILE_TRIAGE_TOPN)
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
    extracted_paths = agent.tools.state.fs_list(scan_id, prefix="extractions/")
    extracted_ids: set[str] = set()
    for p in extracted_paths:
        rec = agent.tools.state.fs_read(scan_id, p)
        if isinstance(rec, dict) and rec.get("arxiv_id"):
            extracted_ids.add(rec["arxiv_id"])
    missing_ids = expected_ids - extracted_ids
    if not missing_ids:
        return
    if len(missing_ids) > BACKFILL_MAX:  # likely infra issue; inline retry won't help
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
    paper_by_id = {
        p.get("arxiv_id"): p
        for p in top_n_raw
        if isinstance(p, dict) and p.get("arxiv_id")
    }
    from domains.settings.chat import service as chat_service

    chain = chat_service.build_chat_model()
    try: runtime.llm_counter.service.set_phase("deep_read")  # bucket backfill calls under deep_read in drawer KPIs
    except Exception: pass

    # 2026-09-17: was a sequential `for` loop — the one place in RR that
    # didn't match DD/YCS's gathered-concurrency pattern for "N
    # independent items" (same shape as `graph_build_papers`'
    # Semaphore+gather, just never ported here). BACKFILL_MAX already
    # caps this at 3 items, so the semaphore is a safety margin rather
    # than a real rate-limit need — matches graph_build's default
    # concurrency (4) rather than inventing a new number.
    sem = asyncio.Semaphore(min(4, max(1, BACKFILL_MAX)))

    async def _guarded(arxiv_id: str, paper: dict[str, Any]) -> bool:
        async with sem:
            try:
                await _backfill_one(scan_id, arxiv_id, paper, chain)
                return True
            except Exception as e:
                logger.warning(
                    f"[rr-task] backfill failed for {arxiv_id}: "
                    f"{type(e).__name__}: {e}"
                )
                return False

    results = await asyncio.gather(*(
        _guarded(arxiv_id, paper_by_id[arxiv_id])
        for arxiv_id in sorted(missing_ids)
        if isinstance(paper_by_id.get(arxiv_id), dict)
    ))
    backfilled = sum(1 for ok in results if ok)
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
    """Run one inline deep_read extraction via the bandit chain. Raises on failure."""
    from langchain_core.messages import HumanMessage, SystemMessage

    title    = (paper.get("title")    or "").strip()
    abstract = (paper.get("abstract") or "").strip()
    if not abstract:
        raise RuntimeError(f"no abstract on disk for {arxiv_id}")

    system_prompt = (
        "=== SKILL: paper_extraction ===\n\n"
        f"{agent.skills.service.SKILL_PAPER_EXTRACTION}\n\n"
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
    response = await runtime.service.resilient_ainvoke(
        chain,
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ],
        operation    = "backfill",
        timeout_s    = _BACKFILL_CALL_TIMEOUT_S,
        max_attempts = 2,
    )
    raw = (getattr(response, "content", None) or "").strip()
    if not raw:
        raise RuntimeError("empty content from endpoint")
    if raw.startswith("```"):  # some arms wrap JSON in ```json fences
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
    agent.tools.fs.service.write_extraction.invoke(payload)
    logger.info(
        f"[rr-task] backfill wrote extraction arxiv_id={arxiv_id} "
        f"confidence={payload['confidence']:.2f}"
    )


# ---------------------------------------------------------------------------
# Build-tab code synthesis — dispatched by
# `POST /scan/{scan_id}/finding/{arxiv_id}/code/generate` so the HTTP layer
# never blocks on (or gateway-times-out on) the 1-5+ min generate→critique→
# revise loop. `GET /scan/{scan_id}/finding/{arxiv_id}/code` polls
# `runtime.service.get_code_synth_status` — set here to "running" on start
# and cleared on success (the MinIO cache written just before is then the
# source of truth), or set to "error" on failure so the operator sees a
# Retry button instead of a spinner stuck forever.
# ---------------------------------------------------------------------------

@app.task(
    name          = "domains.rr.task.run_code_synth",
    bind          = True,
    acks_late     = False,
    track_started = True,
)
def run_code_synth(self, scan_id: str, arxiv_id: str, prompt_version: str) -> dict:
    logger.info(
        f"[rr-task] run_code_synth scan_id={scan_id} arxiv_id={arxiv_id} "
        f"prompt_version={prompt_version}"
    )
    try:
        asyncio.run(_run_code_synth_async(scan_id, arxiv_id, prompt_version))
        return {"scan_id": scan_id, "arxiv_id": arxiv_id, "status": "done"}
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.exception(
            f"[rr-task] run_code_synth failed scan_id={scan_id} "
            f"arxiv_id={arxiv_id}: {e}"
        )
        try:
            asyncio.run(
                runtime.service.set_code_synth_error(scan_id, arxiv_id, prompt_version, err)
            )
        except Exception as fe:
            logger.warning(f"[rr-task] run_code_synth set_code_synth_error also failed: {fe}")
        return {"scan_id": scan_id, "arxiv_id": arxiv_id, "status": "failed", "error": err}


async def _run_code_synth_async(scan_id: str, arxiv_id: str, prompt_version: str) -> None:
    """Fetch the finding row, synthesize, cache to MinIO. Raises on any
    failure — the Celery task's except-block turns that into the Redis
    error status the poll endpoint surfaces."""
    finding = await stores.service.get_finding_digest_json(UUID(scan_id), arxiv_id)
    if finding is None:
        raise ValueError(f"finding {arxiv_id!r} not found for scan_id {scan_id}")

    # Best-effort — attributes this task's endpoint calls to the right
    # scan/phase in the per-scan LLM counters, same as the old inline
    # endpoint did (this runs outside the scan's own agent.ainvoke()
    # context, so the contextvars RRLlmCounterCallback reads wouldn't
    # otherwise be set).
    try:
        runtime.llm_counter.service.set_scan(scan_id)
        runtime.llm_counter.service.set_phase("build")
    except Exception:
        pass

    result = await agent.tools.code_synth.service.synth_code(finding)
    await stores.service.put_code_py(scan_id, arxiv_id, prompt_version, result["code"])
    await runtime.service.clear_code_synth_status(scan_id, arxiv_id, prompt_version)
