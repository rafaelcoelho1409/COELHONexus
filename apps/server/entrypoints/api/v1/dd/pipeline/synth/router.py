"""Synth pipeline endpoints. Per-chapter LangGraph runs on Celery
(queue `synth-{env}`). thread_id = `docs-distiller/synth/{slug}/{uuid}`;
chapter_id lives in SynthState, not the thread_id."""
from __future__ import annotations
import ai, domains, infra
import domains.dd.pipeline.ai.task
from . import params, service

import asyncio
import io
import json
import logging
import zipfile
from hashlib import sha256

from fastapi import APIRouter, HTTPException, Query, Response
from starlette.responses import StreamingResponse


logger = logging.getLogger(__name__)


def _planner_review_reasons(health: dict, stats: dict, chapters: list[dict]) -> list[str]:
    reasons = []
    design = stats.get("chapter_design") or {}
    doc_fallback_pct = float(health.get("doc_distill_fallback_pct") or 0.0)
    if doc_fallback_pct >= 40.0:
        reasons.append(f"{doc_fallback_pct:.1f}% of source distillation used fallback extraction")
    proposal_fallback = bool(health.get("chapter_propose_fallback_used"))
    design_fallback = bool(
        health.get("chapter_design_fallback_used") or design.get("fallback_used")
    )
    if health.get("planner_version") == "v2" and proposal_fallback and design_fallback:
        # v2 creates the proposal artifact from chapter design, so these flags
        # describe one fallback event rather than two independent stages.
        reasons.append("chapter design used its deterministic fallback to create the chapter proposals")
    elif proposal_fallback:
        reasons.append("chapter proposal used its deterministic fallback")
    assignment_fallback_pct = float(health.get("chapter_assign_fallback_pct") or 0.0)
    if assignment_fallback_pct >= 50.0:
        reasons.append(f"{assignment_fallback_pct:.1f}% of chapter assignments used fallback logic")
    tutorial_count = sum(
        1 for chapter in chapters
        if isinstance(chapter, dict)
        and chapter.get("kind") != "appendix"
        and chapter.get("role") != "appendix"
    )
    chapter_count = tutorial_count or health.get("chapter_select_n_chapters") or stats.get("n_chapters")
    if health.get("order_chapters_valid_samples") == 0 and chapter_count and chapter_count > 1:
        reasons.append("chapter ordering had no valid model samples")
    if design_fallback and not (
        health.get("planner_version") == "v2" and proposal_fallback
    ):
        reasons.append(
            "chapter design used its deterministic fallback"
            if health.get("planner_version") != "v2"
            else "chapter design used deterministic recovery"
        )
    design_errors = max(
        int(health.get("chapter_design_errors") or 0),
        len(design.get("designer_errors") or []),
    )
    if design_errors:
        reasons.append(f"chapter design recorded {design_errors} structured-output failure(s)")
    review_flags = max(
        int(health.get("chapter_design_review_flags") or 0),
        len(design.get("designer_review_flags") or []),
    )
    review_flag_details = design.get("designer_review_flags") or health.get("chapter_design_review_reasons") or []
    if review_flags:
        detail = next((str(flag).strip() for flag in review_flag_details if str(flag).strip()), "")
        suffix = f": {detail[:220]}" if detail else ""
        reasons.append(f"chapter design has {review_flags} unresolved review flag(s){suffix}")
    if health.get("chapter_design_ledger_available") is False:
        reasons.append("the best-code ledger was unavailable")
    unplaced = int(health.get("chapter_select_unplaced_pages") or 0)
    if unplaced:
        reasons.append(f"{unplaced} source page(s) are unplaced")
    coverage = health.get("chapter_select_page_coverage_pct")
    if coverage is not None and float(coverage) < 100.0 and not unplaced:
        reasons.append(f"source coverage is {float(coverage):.1f}%")
    fallback_pct = float(health.get("page_card_fallback_pct") or 0.0)
    if fallback_pct >= 40.0:
        reasons.append(f"{fallback_pct:.1f}% of page cards used fallback extraction")

    missing_digests = int(health.get("chapter_source_digests_missing") or 0)
    if missing_digests:
        reasons.append(f"{missing_digests} source content fingerprint(s) are missing")
    suggested = health.get("chapter_design_suggested_chapters") or design.get("suggested_chapters")
    if chapter_count and suggested and chapter_count > 2 * suggested:
        reasons.append(f"the plan has {chapter_count} tutorial chapters against a {suggested}-chapter estimate")
    return reasons


router = APIRouter()


async def _source_digest_mismatches(minio, chapters: list[dict]) -> list[str]:
    """Require the current v2 corpus bytes to match the Planner's source snapshot."""
    expected: dict[str, str] = {}
    mismatches: set[str] = set()
    for chapter in chapters:
        sources = chapter.get("sources") or []
        if not sources:
            mismatches.add(str(chapter.get("id") or "chapter without source pages"))
        digests = chapter.get("source_content_digests") or {}
        for source in sources:
            digest = digests.get(source) if isinstance(digests, dict) else None
            if not isinstance(digest, str) or not digest:
                mismatches.add(source)
            elif source in expected and expected[source] != digest:
                mismatches.add(source)
            else:
                expected[source] = digest
    keys = sorted(expected)
    try:
        bodies = await minio.read_many(keys)
    except Exception as error:
        logger.warning(f"[synth] failed verifying Planner source fingerprints: {type(error).__name__}: {error}")
        raise HTTPException(status_code=503, detail="could not verify the current corpus against the Planner snapshot") from error
    for key, body in zip(keys, bodies):
        if not body or sha256(body.encode("utf-8")).hexdigest() != expected[key]:
            mismatches.add(key)
    if len(bodies) != len(keys):
        mismatches.update(keys[len(bodies):])
    return sorted(mismatches)


@router.get("/info")
async def synth_info(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    graph = ai.dd.graphs.pipeline.synth.graphs.synth_graph(checkpointer = None)
    return {
        "node_order":  list(domains.dd.pipeline.ai.synth.service.node_order()),
        "implemented": list(domains.dd.pipeline.ai.synth.service.node_order()),
        "synth_version": ai.dd.graphs.pipeline.synth.params.version(),
        "graph_mermaid": ai.dd.graphs.topology.mermaid_source(graph),
        "modes": [
            {"key": "quality", "label": "Quality (default)", "enabled": True},
            {"key": "fast",    "label": "Fast (3 iters)",    "enabled": False},
        ],
        "status": "live",
    }


@router.get("/{slug}/study/chapters")
async def list_study_chapters(slug: str, response: Response) -> dict:
    """Drives the Study sidebar. `rendered` flags MUST NOT be cached —
    a stale 200 after a wipe would show phantom synthesized chapters."""
    response.headers["Cache-Control"] = "no-store"
    plan = await service.load_plan(slug)
    chapters_in: list[dict] = plan.get("chapters") or []
    if not chapters_in:
        return {"framework_slug": slug, "chapters": []}

    minio = infra.minio.service.get_storage()

    # Persisted timing roll-up (per-chapter wall + study total) so the
    # sidebar + navbar show times after a refresh / for cached studies.
    per_chapter_ms: dict = {}
    study_total_wall_ms = 0
    try:
        _t = json.loads(
            await minio.read_text(domains.dd.pipeline.storage.keys.study_timing_key(slug))
        )
        per_chapter_ms = _t.get("per_chapter_ms") or {}
        study_total_wall_ms = int(_t.get("total_wall_ms") or 0)
    except Exception:
        pass

    out: list[dict] = []
    for ch in chapters_in:
        cid = (ch or {}).get("id")
        if not cid:
            continue
        render_key = domains.dd.pipeline.storage.keys.chapter_render_latest_key(
            slug, cid, ai.dd.graphs.pipeline.synth.params.version(),
        )
        rendered = await minio.exists(render_key)
        entry: dict = {
            "id":         cid,
            "title":      ch.get("title") or cid,
            "order":      ch.get("order") or 0,
            "n_sources":  len(ch.get("sources") or []),
            "source_files": [str(key).rsplit("/", 1)[-1] for key in ch.get("sources") or []],
            "synth_eligible": ch.get("synth_eligible", True) is not False,
            "synth_exclusion_reason": ch.get("synth_exclusion_reason") or "",
            "rendered":   rendered,
            "audit_passed": False,
            "needs_review": False,
            "render_path": render_key if rendered else None,
            "wall_ms":    int(per_chapter_ms.get(cid, 0) or 0),
        }
        if rendered:
            try:
                text = await minio.read_text(render_key)
                rp = json.loads(text)
                if ai.dd.nodes.pipeline.synth.render.domain.render_is_stale(rp, ch):
                    # Built under an older plan: don't present it as this chapter.
                    entry["rendered"] = False
                    entry["render_path"] = None
                    entry["stale"] = True
                    out.append(entry)
                    continue
                entry["audit_passed"] = bool(
                    (rp.get("audit") or {}).get("audit_passed", False)
                )
                entry["rendered"] = entry["audit_passed"] is True
                entry["needs_review"] = not entry["audit_passed"]
                if not entry["rendered"]:
                    entry["render_path"] = None
                for _k in ("n_sources_cited", "n_code_available", "n_code_used"):
                    if _k in rp:
                        entry[_k] = rp[_k]
                entry["rendered_chars"] = rp.get("rendered_chars", 0)
                entry["n_sections"] = rp.get("n_sections", 0)
                entry["n_code_files"] = len(rp.get("code_files") or [])
                entry["thread_id"] = rp.get("thread_id") or None
            except Exception:
                entry["rendered"] = False
                entry["needs_review"] = True
                entry["render_path"] = None
        out.append(entry)
    return {
        "framework_slug": slug,
        "chapters": out,
        "study_total_wall_ms": study_total_wall_ms,
    }


@router.get("/{slug}/active")
async def synth_active(slug: str, response: Response) -> dict:
    """Page-refresh recovery: returns the study orchestrator's thread_id
    so the UI can reconnect to its SSE without browser localStorage. A single-chapter run (the per-chapter ▶ button on the
    chapter list) has no study registry entry; its thread is the value of the slug's synth lock, returned as `chapter_thread_id`."""
    response.headers["Cache-Control"] = "no-store"
    return await domains.dd.pipeline.runtime.dispatch.service.read_active_synth(slug)


@router.get("/{slug}/study/{chapter_id}/artifact/{artifact_name}")
async def get_study_artifact(
    slug: str, chapter_id: str, artifact_name: str,
) -> StreamingResponse:
    """VALID_ARTIFACTS allow-list prevents arbitrary MinIO key reads."""
    if artifact_name not in params.VALID_ARTIFACTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"invalid artifact name {artifact_name!r}; valid: "
                f"{sorted(params.VALID_ARTIFACTS)}"
            ),
        )
    key = domains.dd.pipeline.storage.keys.chapter_artifact_key(slug, chapter_id, artifact_name)
    minio = infra.minio.service.get_storage()
    if not await minio.exists(key):
        raise HTTPException(
            status_code=404,
            detail=(
                f"artifact {artifact_name!r} for chapter {chapter_id!r} "
                f"not in MinIO at {key!r}; run synth + render first"
            ),
        )

    async def _gen():
        try:
            text = await minio.read_text(key)
            yield text.encode("utf-8")
        except Exception as e:
            logger.warning(
                f"[synth-study-artifact] read failed for {key!r}: "
                f"{type(e).__name__}: {e}"
            )
            yield b""

    return StreamingResponse(
        _gen(),
        media_type=params.VALID_ARTIFACTS[artifact_name],
        headers={
            "Cache-Control": "public, max-age=60",
        },
    )


@router.get("/{slug}/study/{chapter_id}/code.zip")
async def get_study_code(slug: str, chapter_id: str) -> StreamingResponse:
    """The chapter's `code/` folder (each step's examples, the capstone and its `main.py`) as one zip. Only a tutorial chapter has one."""
    minio = infra.minio.service.get_storage()
    root = domains.dd.pipeline.storage.keys.chapter_artifact_key(slug, chapter_id, "")
    names = sorted(await minio.list(domains.dd.pipeline.storage.keys.chapter_artifact_key(slug, chapter_id, "code/")))
    if not names:
        raise HTTPException(
            status_code=404,
            detail=f"no code folder for chapter {chapter_id!r} under {root!r}; only a tutorial chapter, once rendered, has one",
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in zip(names, await minio.read_many(names)):
            archive.writestr(name[len(root):], text or "")
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{chapter_id}-code.zip"', "Cache-Control": "no-store"},
    )


@router.get("/recent")
async def list_recent_synth() -> dict:
    """Most-recent thread per slug for page-refresh recovery."""
    out: list[dict] = []
    try:
        with infra.postgres.service.span(
            "select", **{"db.postgres.table": "checkpoints"},
        ):
            async with await infra.postgres.service.connect() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        WITH thread_stats AS (
                            SELECT
                                split_part(thread_id, '/', 3) AS slug,
                                thread_id,
                                count(*)           AS ckpt_count,
                                max(checkpoint_id) AS latest_ckpt
                            FROM checkpoints
                            WHERE thread_id LIKE 'docs-distiller/synth/%'
                            GROUP BY thread_id
                        )
                        SELECT DISTINCT ON (slug)
                            slug, thread_id, ckpt_count, latest_ckpt
                        FROM thread_stats
                        ORDER BY slug, ckpt_count DESC, latest_ckpt DESC
                    """)
                    rows = await cur.fetchall()
        for slug, tid, ckpt_count, latest in rows:
            out.append({
                "slug":          slug,
                "thread_id":     tid,
                "checkpoint_id": str(latest),
                "ckpt_count":    int(ckpt_count),
            })
    except Exception as e:
        logger.warning(f"[synth-recent] query failed: {e}")
    return {"recent": out}


@router.post("/{slug}")
async def start_synth(
    slug: str,
    chapter_id: str | None = Query(default=None),
    mode: str = Query(default="quality"),
    thread_id: str | None = Query(default=None),
) -> dict:
    """No chapter_id → STUDY mode (orchestrator runs all chapters);
    with chapter_id → single-chapter escape hatch."""
    if mode not in params.VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid mode {mode!r}; expected one of {sorted(params.VALID_MODES)}",
        )

    plan = await service.load_plan(slug)
    stats = (plan or {}).get("stats") or {}
    health = stats.get("pipeline_health") or {}
    if health.get("degraded") is True:
        reasons = _planner_review_reasons(health, stats, (plan or {}).get("chapters") or [])
        review_detail = "; ".join(reasons) if reasons else "the plan has an unresolved Planner quality issue"
        raise HTTPException(
            status_code=409,
            detail=(
                f"the latest Planner plan needs review before Synth can run: {review_detail}. "
                "Fix the Planner issues and run Planner again"
            ),
        )
    # The planner's chapter array is already in pedagogical order. Sorting ids
    # made the study run a different chapter first (and shuffled the UI list).
    all_plan_chapter_ids: list[str] = [
        chapter["id"]
        for chapter in (plan.get("chapters") or [])
        if isinstance(chapter, dict) and chapter.get("id")
    ]
    eligible_chapter_ids = {
        chapter["id"]
        for chapter in (plan.get("chapters") or [])
        if isinstance(chapter, dict) and chapter.get("id") and chapter.get("synth_eligible", True) is not False
    }
    plan_chapter_ids = [chapter_id for chapter_id in all_plan_chapter_ids if chapter_id in eligible_chapter_ids]
    if not all_plan_chapter_ids:
        raise HTTPException(
            status_code=404,
            detail=f"plan for {slug!r} has no chapters",
        )

    selected_chapter_ids = {chapter_id} if chapter_id else set(plan_chapter_ids)
    chapters_to_run = [
        chapter
        for chapter in (plan.get("chapters") or [])
        if isinstance(chapter, dict)
        and chapter.get("id") in selected_chapter_ids
        and chapter.get("synth_eligible", True) is not False
    ]
    if plan.get("planner_version") == "v2":
        mismatches = await _source_digest_mismatches(infra.minio.service.get_storage(), chapters_to_run)
        if mismatches:
            raise HTTPException(
                status_code=409,
                detail=(
                    "the current corpus differs from the Planner snapshot; run Planner again before Synth. "
                    f"Unmatched source pages: {mismatches[:8]}"
                ),
            )

    if chapter_id is None:
        if not plan_chapter_ids:
            raise HTTPException(status_code=409, detail="the plan has no chapters eligible for Synth")
        study_thread_id = thread_id or domains.dd.pipeline.runtime.dispatch.domain.make_study_thread_id(slug)

        gate = await domains.dd.pipeline.runtime.dispatch.service.reserve_synth_start(slug, study_thread_id)
        if gate is not None:
            return gate

        try:
            async_result = domains.dd.pipeline.ai.task.run_study.delay(
                study_thread_id, slug, plan_chapter_ids, mode,
            )
            infra.opentelemetry.service.tag_celery_task(async_result.id)
        except Exception as e:
            await domains.dd.pipeline.runtime.dispatch.service.release_synth_start(slug)
            logger.exception(
                f"[synth-study] {study_thread_id}: celery dispatch "
                f"failed: {type(e).__name__}: {e}"
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    f"celery dispatch failed: "
                    f"{type(e).__name__}: {e}"
                ),
            )

        await domains.dd.pipeline.runtime.dispatch.service.register_active_study(slug, study_thread_id)

        return {
            "study_thread_id": study_thread_id,
            "slug":            slug,
            "n_chapters":      len(plan_chapter_ids),
            "chapter_ids":     plan_chapter_ids,
            "mode":            mode,
            "concurrency":     ai.dd.graphs.pipeline.synth.params.study_sem(),
            "status":          "queued",
            "celery_task_id":  async_result.id,
            "latency_ms":      0,
        }

    if chapter_id not in set(all_plan_chapter_ids):
        raise HTTPException(
            status_code=404,
            detail=(
                f"chapter {chapter_id!r} not in plan; known ids: "
                f"{plan_chapter_ids}"
            ),
        )

    if chapter_id not in eligible_chapter_ids:
        raise HTTPException(
            status_code=409,
            detail="this chapter is marked reference-only and is not eligible for Synth",
        )

    if not thread_id:
        thread_id = domains.dd.pipeline.runtime.dispatch.domain.make_chapter_thread_id(slug)

    gate = await domains.dd.pipeline.runtime.dispatch.service.reserve_synth_start(slug, thread_id)
    if gate is not None:
        return gate

    try:
        async_result = domains.dd.pipeline.ai.task.run_single_chapter.delay(
            thread_id, slug, chapter_id, mode,
        )
        infra.opentelemetry.service.tag_celery_task(async_result.id)
    except Exception as e:
        await domains.dd.pipeline.runtime.dispatch.service.release_synth_start(slug)
        logger.exception(
            f"[synth] {thread_id}: celery dispatch failed: "
            f"{type(e).__name__}: {e}"
        )
        raise HTTPException(
            status_code=503,
            detail=f"celery dispatch failed: {type(e).__name__}: {e}",
        )

    return {
        "thread_id":      thread_id,
        "slug":           slug,
        "chapter_id":     chapter_id,
        "mode":           mode,
        "status":         "queued",
        "celery_task_id": async_result.id,
        "latency_ms":     0,
    }


@router.post("/{thread_id:path}/resume")
async def resume_synth(thread_id: str) -> dict:
    async with infra.redis.service.client() as r:
        await domains.dd.pipeline.runtime.cancel.service.clear_cancel(r, thread_id)

    try:
        async_result = domains.dd.pipeline.ai.task.resume_synth.delay(thread_id)
        infra.opentelemetry.service.tag_celery_task(async_result.id)
    except Exception as e:
        logger.exception(
            f"[synth] {thread_id}: celery resume dispatch failed: "
            f"{type(e).__name__}: {e}"
        )
        raise HTTPException(
            status_code=503,
            detail=f"celery dispatch failed: {type(e).__name__}: {e}",
        )

    return {
        "thread_id":      thread_id,
        "status":         "queued",
        "celery_task_id": async_result.id,
    }


@router.post("/{thread_id:path}/cancel")
async def cancel_synth(thread_id: str) -> dict:
    """A STUDY thread cancel must propagate to each in-flight per-chapter
    thread — chapter watchers poll their own flag, not the study flag.
    Without propagation only the NEXT chapter is blocked; the in-flight
    one keeps firing LLM calls."""
    propagated_to = await domains.dd.pipeline.runtime.dispatch.service.cancel_synth_run(thread_id)
    return {
        "thread_id":     thread_id,
        "status":        "cancel_requested",
        "propagated_to": propagated_to,
    }


@router.get("/{thread_id:path}/events")
async def synth_events(thread_id: str) -> StreamingResponse:
    """Initial `: stream open` comment forces proxies to flush headers
    (avoid first-event delay). Heartbeat every 15s keeps the connection
    alive through k3d/traefik (default idle-stream timeout 60s) during
    long Synth gaps (sawc_write, book_harmonize)."""
    _SSE_HEARTBEAT_S = 15.0
    _DONE = object()

    async def _gen():
        yield b": stream open\n\n"
        queue: asyncio.Queue = asyncio.Queue()

        async def _pump():
            try:
                async for event in domains.dd.pipeline.runtime.progress.service.subscribe_progress(thread_id):
                    await queue.put(event)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(
                    f"[synth-events] {thread_id}: pump crashed "
                    f"({type(e).__name__}: {e})"
                )
            finally:
                await queue.put(_DONE)

        pump_task = asyncio.create_task(_pump())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=_SSE_HEARTBEAT_S,
                    )
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                if event is _DONE:
                    return
                try:
                    payload = json.dumps(event, default=str)
                except Exception:
                    continue
                yield f"data: {payload}\n\n".encode("utf-8")
        except asyncio.CancelledError:
            return
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache, no-transform",
            "Connection":        "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/debug/graph/{thread_id:path}/state")
async def synth_state(thread_id: str) -> dict:
    try:
        graph = domains.dd.pipeline.ai.synth.service.synth_graph()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    config = {"configurable": {"thread_id": thread_id}}
    snap = await graph.aget_state(config)
    if snap.values == {}:
        raise HTTPException(
            status_code=404,
            detail=f"no checkpoints for thread_id={thread_id!r}",
        )
    return {
        "thread_id":   thread_id,
        "values":      dict(snap.values or {}),
        "next":        list(snap.next or []),
        "config":      snap.config,
        "metadata":    snap.metadata,
        "created_at":  str(snap.created_at) if snap.created_at else None,
    }


@router.get("/debug/graph/{thread_id:path}/llm-counters")
async def synth_llm_counters(thread_id: str) -> dict:
    return await domains.settings.llm_counter.service.read_counters("dd", thread_id)


@router.get("/debug/graph/{thread_id:path}/history")
async def synth_history(thread_id: str) -> dict:
    try:
        graph = domains.dd.pipeline.ai.synth.service.synth_graph()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    config = {"configurable": {"thread_id": thread_id}}
    history: list[dict] = []
    async for snap in graph.aget_state_history(config):
        history.append({
            "checkpoint_id": (snap.config or {})
                .get("configurable", {})
                .get("checkpoint_id"),
            "next":          list(snap.next or []),
            "metadata":      snap.metadata,
            "created_at":    str(snap.created_at) if snap.created_at else None,
            "state_keys":    sorted((snap.values or {}).keys()),
        })
    return {"thread_id": thread_id, "history": history}


@router.delete("/{slug}/wipe")
async def wipe_synth(slug: str, chapter_id: str | None = Query(default=None)) -> dict:
    """Wipes MinIO dd/{slug}/synth/, Postgres checkpoints for synth+study
    threads, Redis SSE snapshots + lock. Without the Redis sweep a wiped
    slug "comes back from the dead" via the cached study SSE snapshot.
    With `chapter_id`, wipes only that chapter's MinIO outputs (see
    `service.wipe_one_chapter`)."""
    if not slug or "/" in slug:
        raise HTTPException(
            status_code=400,
            detail=f"invalid slug {slug!r}; slashes not allowed",
        )
    if chapter_id is not None:
        return await service.wipe_one_chapter(slug, chapter_id)

    minio = infra.minio.service.get_storage()
    try:
        n_minio = await minio.delete_prefix(domains.dd.pipeline.storage.keys.synth_prefix(slug))
    except Exception as e:
        logger.warning(f"[synth-wipe] MinIO delete failed for {slug!r}: {e}")
        n_minio = -1

    patterns = [
        f"docs-distiller/synth/{slug}/%",
        f"docs-distiller/study/{slug}/%",
    ]
    counts: dict = {}
    try:
        with infra.postgres.service.span(
            "delete", **{"db.postgres.table": "checkpoints,checkpoint_writes,checkpoint_blobs"},
        ):
            async with await infra.postgres.service.connect(autocommit=True) as conn:
                for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    async with conn.cursor() as cur:
                        try:
                            rows = 0
                            for pat in patterns:
                                await cur.execute(
                                    f"DELETE FROM {tbl} WHERE thread_id LIKE %s",
                                    (pat,),
                                )
                                rows += cur.rowcount
                            counts[tbl] = rows
                        except Exception as e:
                            counts[tbl] = f"skipped: {type(e).__name__}: {e}"
    except Exception as e:
        logger.warning(f"[synth-wipe] Postgres delete failed for {slug!r}: {e}")
        counts["error"] = f"{type(e).__name__}: {e}"

    n_redis = await domains.dd.pipeline.runtime.dispatch.service.wipe_synth_redis(slug)

    logger.info(
        f"[synth-wipe] {slug}: minio={n_minio} blobs, postgres={counts}, "
        f"redis={n_redis} keys"
    )
    return {
        "slug":                  slug,
        "minio_blobs_deleted":   n_minio,
        "postgres_rows_deleted": counts,
        "redis_keys_deleted":    n_redis,
    }
