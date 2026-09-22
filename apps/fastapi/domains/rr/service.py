"""I/O orchestration for the RR domain — Imperative Shell.

Per docs/CODE-CONVENTIONS.md §service: thin orchestrator. Pure math
lives in domain.py; per-store ops live under stores/. This module
composes them into the operations the agent (graph_build, report) and
the FastAPI router will call.
"""
from __future__ import annotations
import domains
import infra
from . import agent, domain, entities, keys, params, runtime, stores

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID


logger = logging.getLogger(__name__)


async def bootstrap_stores() -> None:
    """Initialize all 4 RR stores. Idempotent + safe to re-run."""
    results = await asyncio.gather(
        stores.service.bootstrap_postgres(),
        stores.service.bootstrap_neo4j(),
        stores.service.bootstrap_qdrant(),
        stores.service.bootstrap_minio(),
    )
    logger.info(f"[rr-service] bootstrap_stores: 4/4 complete ({len(results)})")


async def persist_paper(
    paper: entities.NormalizedPaper,
    *,
    embedding: list[float] | tuple[float, ...] | None,
    signal: float | None = None,
) -> None:
    """Write a paper to Neo4j (always) + Qdrant (only when embedding given)."""
    if not paper.arxiv_id:
        logger.warning(
            f"[rr-service] persist_paper skipped — no arxiv_id "
            f"(title={paper.title[:40]!r})"
        )
        return
    coros: list[Any] = [stores.service.upsert_paper(paper, signal=signal)]
    if embedding is not None:
        coros.append(
            stores.service.upsert_paper_vector(paper, embedding=embedding, signal=signal)
        )
    await asyncio.gather(*coros)


async def begin_scan(
    scan_id:    UUID,
    profile_id: str,
    *,
    topic:      str | None       = None,
    verticals:  list[str] | None = None,
    top_n:      int | None       = None,
) -> None:
    """Create the radar_scans row (status=pending) then immediately flip to running."""
    await stores.service.create_scan(
        scan_id, profile_id,
        topic     = topic,
        verticals = verticals,
        top_n     = top_n,
    )
    await stores.service.mark_scan_running(scan_id)


async def complete_scan(
    scan_id: UUID,
    *,
    total_candidates: int,
    total_in_digest: int,
) -> None:
    await stores.service.mark_scan_done(
        scan_id,
        total_candidates = total_candidates,
        total_in_digest  = total_in_digest,
    )


async def fail_scan(scan_id: UUID, error: str, *, cancelled: bool = False) -> None:
    status = keys.SCAN_STATUS_CANCELLED if cancelled else keys.SCAN_STATUS_ERROR
    await stores.service.mark_scan_error(scan_id, status=status, error=error)


async def delete_scan(scan_id: UUID) -> dict:
    """Drop per-scan presentation artifacts: Postgres row + findings + MinIO digest.

    Intentionally preserves accumulated knowledge: Neo4j graph, Qdrant embeddings, radar_seen markers.
    """
    pg_deleted    = False
    minio_deleted = False
    code_deleted  = 0
    try:
        pg_deleted = await stores.service.delete_scan_record(scan_id)
    except Exception as e:
        logger.warning(f"[rr-service] delete_scan {scan_id} pg failed: {e}")
    try:
        minio_deleted = await stores.service.delete_digest_json(str(scan_id))
    except Exception as e:
        logger.warning(f"[rr-service] delete_scan {scan_id} minio failed: {e}")
    try:
        code_deleted = await stores.service.delete_code_dir(str(scan_id))
    except Exception as e:
        logger.warning(f"[rr-service] delete_scan {scan_id} code failed: {e}")
    logger.info(
        f"[rr-service] delete_scan {scan_id} pg={pg_deleted} "
        f"minio={minio_deleted} code={code_deleted}"
    )
    return {
        "scan_id": str(scan_id),
        "pg":      pg_deleted,
        "minio":   minio_deleted,
        "code":    code_deleted,
    }


async def cancel_scan(scan_id: UUID, *, reason: str = "cancelled by user") -> bool:
    """Revoke the Celery task, mark Postgres cancelled, emit terminal SSE event.

    Returns True when a live task was found and revoked, False when no task_id registered.
    Order: revoke → mark Postgres → emit SSE → drop task_id key.
    A failure in step 2/3/4 doesn't roll back step 1; the worker is already dead.
    """
    import infra.celery.service

    task_id = await runtime.service.get_task_id(str(scan_id))
    if not task_id:
        logger.info(f"[rr-service] cancel_scan {scan_id}: no task_id found")
        return False

    try:
        infra.celery.service.app.control.revoke(task_id, terminate=True, signal="SIGTERM")
        logger.info(f"[rr-service] cancel_scan {scan_id} revoked task_id={task_id}")
    except Exception as e:
        logger.warning(
            f"[rr-service] cancel_scan {scan_id} revoke failed "
            f"task_id={task_id}: {type(e).__name__}: {e}"
        )

    try:
        await fail_scan(scan_id, reason, cancelled=True)
    except Exception as e:
        logger.warning(f"[rr-service] cancel_scan {scan_id} fail_scan failed: {e}")

    try:
        await runtime.service.emit_event(str(scan_id), "cancelled", message=reason)
    except Exception as e:
        logger.warning(f"[rr-service] cancel_scan {scan_id} emit_event failed: {e}")

    try:
        await runtime.service.clear_task_id(str(scan_id))
    except Exception:
        pass

    return True


async def persist_scan_result(
    scan_id: UUID,
    profile_id: str,
    *,
    findings: list[entities.Finding],
    digest_payload: dict[str, Any],
) -> dict[str, Any]:
    """Persist ranked digest: findings → Postgres, digest.json → MinIO, arxiv_ids → radar_seen."""
    n_findings = await stores.service.record_findings(scan_id, findings)
    digest_key = await stores.service.put_digest_json(str(scan_id), digest_payload)
    try:
        await stores.service.write_synthesis_meta(
            scan_id,
            themes  = list(digest_payload.get("themes") or []),
            summary = digest_payload.get("summary"),
        )
    except Exception as e:
        logger.warning(
            f"[rr-service] persist_scan_result synthesis meta failed: "
            f"{type(e).__name__}: {e}"
        )
    arxiv_ids = [f.arxiv_id for f in findings if f.arxiv_id]
    n_seen = await stores.service.mark_seen_batch(profile_id, arxiv_ids)
    summary = {
        "scan_id":           str(scan_id),
        "profile_id":        profile_id,
        "n_findings":        n_findings,
        "n_marked_seen":     n_seen,
        "digest_minio_key":  digest_key,
        "persisted_at":      datetime.now(timezone.utc).isoformat(),
    }
    logger.info(
        f"[rr-service] persist_scan_result {scan_id} "
        f"findings={n_findings} seen={n_seen} key={digest_key}"
    )
    return summary


async def get_seen_ids(profile_id: str) -> frozenset[str]:
    return await stores.service.get_seen_ids(profile_id)


async def get_profile(profile_id: str) -> dict[str, Any] | None:
    return await stores.service.get_profile(profile_id)


async def upsert_profile(
    profile_id: str, *, interests: dict[str, Any], weights: dict[str, Any],
) -> None:
    await stores.service.upsert_profile(
        profile_id, interests=interests, weights=weights,
    )


async def run_scan_async(
    scan_id: str,
    profile_id: str,
    topic: str,
    verticals: list[str],
    top_n: int,
) -> dict:
    """Span + metrics wrapper around the RR scan orchestration."""
    t0 = asyncio.get_running_loop().time()
    with infra.langfuse.sessions.session(
        "rr",
        session_id = scan_id,
        user_id    = profile_id,
        digest_id  = scan_id,
    ):
        with infra.otel.service.get_tracer().start_as_current_span(
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
            infra.langfuse.spans.set_current_span_langfuse_io(input_data = {
                "topic": topic,
                "verticals": list(verticals or []),
                "top_n": top_n,
                "scan_id": scan_id,
                "profile_id": profile_id,
            })
            infra.langfuse.spans.set_current_span_langfuse_trace_metadata({
                "pipeline": "rr_scan",
                "scan_id": scan_id,
                "profile_id": profile_id,
                "vertical_count": len(verticals),
                "top_n": top_n,
            })
            infra.langfuse.spans.set_current_span_langfuse_observation_metadata({
                "topic": topic[:200],
                "vertical_count": len(verticals),
            })
            try:
                result = await run_scan_pipeline(
                    scan_id = scan_id,
                    profile_id = profile_id,
                    topic = topic,
                    verticals = verticals,
                    top_n = top_n,
                )
            except Exception as e:
                infra.langfuse.spans.set_current_span_langfuse_io(output_data = {
                    "status": "failed",
                    "error": f"{type(e).__name__}: {e}",
                    "n_findings": 0,
                    "total_candidates": 0,
                    "themes": [],
                    "degraded": True,
                })
                raise
            infra.langfuse.spans.set_current_span_langfuse_io(output_data = {
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
    runtime.observability.metrics.record_scan_run(
        degraded = bool(result.get("degraded", result.get("status") != "done")),
        outcome = str(result.get("status") or "unknown"),
        duration_s = max(asyncio.get_running_loop().time() - t0, 0.0),
        findings = int(result.get("n_findings", 0) or 0),
        candidates = int(result.get("total_candidates", result.get("n_findings", 0)) or 0),
        theme_count = len(result.get("themes") or []),
    )
    return result


async def run_scan_pipeline(
    scan_id: str,
    profile_id: str,
    topic: str,
    verticals: list[str],
    top_n: int,
) -> dict:
    """End-to-end async pipeline. Emits phase events as it goes."""
    scan_uuid = UUID(scan_id)

    await begin_scan(
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
        from domains.rr.agent import graph
        radar_agent = await graph.build_radar_agent()
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

        with infra.otel.service.get_tracer().start_as_current_span(
            "rr.node.backfill",
            attributes={"coelho.langfuse.keep": True, "rr.scan_id": scan_id},
        ):
            try:
                await backfill_missing_extractions(scan_id)
            except Exception as e:
                logger.warning(
                    f"[rr-task] backfill_missing_extractions threw "
                    f"{type(e).__name__}: {e}"
                )

        with infra.otel.service.get_tracer().start_as_current_span(
            "rr.node.digest_assemble",
            attributes={"coelho.langfuse.keep": True, "rr.scan_id": scan_id},
        ):
            digest = build_digest_from_fs(scan_id)
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

            seen_ids = await get_seen_ids(profile_id)
            items = digest.get("items") or []
            for item in items:
                aid = item.get("arxiv_id")
                item["is_new"] = bool(aid) and aid not in seen_ids

            findings = [domain.item_to_finding(it) for it in items]
            await persist_scan_result(
                scan_uuid, profile_id,
                findings       = findings,
                digest_payload = digest,
            )
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
            await fail_scan(scan_uuid, err)
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
        try:
            await infra.neo4j.service.close_neo4j()
        except Exception as e:
            logger.warning(f"[rr-task] infra.neo4j.service.close_neo4j failed: {e}")
        try:
            await infra.qdrant.service.close_qdrant()
        except Exception as e:
            logger.warning(f"[rr-task] infra.qdrant.service.close_qdrant failed: {e}")


def build_digest_from_fs(scan_id: str) -> dict[str, Any] | None:
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


async def backfill_missing_extractions(scan_id: str) -> None:
    """Recover extractions missing from fs up to params.BACKFILL_MAX. No-op when complete or gap > cap."""
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
    if len(missing_ids) > params.BACKFILL_MAX:  # likely infra issue; inline retry won't help
        logger.warning(
            f"[rr-task] backfill skipped scan_id={scan_id} "
            f"missing={len(missing_ids)} > params.BACKFILL_MAX={params.BACKFILL_MAX} "
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

    chain = domains.settings.chat.service.build_chat_model()
    try: runtime.llm_counter.service.set_phase("deep_read")  # bucket backfill calls under deep_read in drawer KPIs
    except Exception: pass

    # 2026-09-17: was a sequential `for` loop — the one place in RR that
    # didn't match DD/YCS's gathered-concurrency pattern for "N
    # independent items" (same shape as `graph_build_papers`'
    # Semaphore+gather, just never ported here). params.BACKFILL_MAX already
    # caps this at 3 items, so the semaphore is a safety margin rather
    # than a real rate-limit need — matches graph_build's default
    # concurrency (4) rather than inventing a new number.
    sem = asyncio.Semaphore(min(4, max(1, params.BACKFILL_MAX)))

    async def _guarded(arxiv_id: str, paper: dict[str, Any]) -> bool:
        async with sem:
            try:
                await backfill_one(scan_id, arxiv_id, paper, chain)
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


async def backfill_one(
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
        timeout_s    = params.BACKFILL_CALL_TIMEOUT_S,
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


async def run_code_synth_async(scan_id: str, arxiv_id: str, prompt_version: str) -> None:
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
