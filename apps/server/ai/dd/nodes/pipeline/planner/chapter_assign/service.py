"""chapter_assign steps — the nodes of its subgraph: prepare, one scorer per page, tally (sweep), finalize.

Every page is scored by the page_scorer agent against every chapter of the run (a confidence per chapter, so a page may belong to
several). One page's ladder, in order: ask; ask again after a transient failure; ask for the chapters a partial answer skipped;
ask again, up to ZERO_REASKS times, when every confidence came back below RESCUE_FLOOR (an answer without content, not a verdict);
and, when the model gave nothing usable, a lexical placement by word overlap so the page still reaches chapter_select. Scores are
stored per page, so a run that stops resumes with the pages it has not scored."""
from __future__ import annotations
import domains
from . import domain, params, versions
from .. import chapter_propose, shared
from ..... import agents, graphs

import asyncio
import json
import logging
import time
from typing import Any

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)

Context = graphs.pipeline.planner.context.PlannerContext
STEP = "chapter_assign"


def _valid_scores(assignment: Any, n: int) -> list[dict]:
    return [
        {"chapter_idx": s.chapter_idx, "confidence": s.confidence}
        for s in assignment.scores
        if 0 <= s.chapter_idx < n
    ]


def _best(scores: list[dict]) -> float:
    return max((s["confidence"] for s in scores), default = 0.0)


async def prepare(state: graphs.pipeline.planner.chapter_assign.state.AssignState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    slug = state.get("framework_slug")
    relevant_files = state.get("relevant_files") or state.get("raw_files") or []
    proposals_ref = state.get("chapter_proposals_ref")
    if not slug or not relevant_files or not proposals_ref:
        return {"finished": True, "chapter_doc_assignments_ref": None, "assign_stats": {"skipped": "missing_inputs"}}

    started = time.monotonic()
    proposals = (await domains.dd.pipeline.storage.service.load_proposals(blobs, slug)).get("proposals") or []
    if not proposals:
        return {"finished": True, "chapter_doc_assignments_ref": None, "assign_stats": {"skipped": "no_proposals_loaded"}}
    distillates = await domains.dd.pipeline.storage.service.load_distillates(blobs, slug)

    proposals_fp = chapter_propose.domain.fingerprint({"proposals": proposals, "distillates": distillates})
    manifest = domain.manifest_hash(slug = slug, proposals_ref = proposals_ref, source_keys = relevant_files, content_fp = proposals_fp)
    vkey = domains.dd.pipeline.storage.keys.assignments_versioned_key(slug, manifest)
    lkey = domains.dd.pipeline.storage.keys.assignments_latest_key(slug)
    if await blobs.exists(vkey) and await blobs.exists(lkey):
        try:
            cached = json.loads(await blobs.read_text(vkey))
            wall_ms = shared.service.elapsed_ms(started)
            stats = {
                "n_docs": len(cached.get("assignments") or {}),
                "n_proposals": len(proposals),
                "cache_hit": True,
                "wall_ms": wall_ms,
                "manifest_hash": manifest,
            }
            shared.service.emit(STEP, "done", cache_hit = True, n_docs = stats["n_docs"], wall_ms = wall_ms)
            return {"finished": True, "chapter_doc_assignments_ref": lkey, "assign_stats": stats}
        except Exception:
            pass

    shared.service.emit(STEP, "start", n_docs = len(relevant_files), n_proposals = len(proposals))

    paths: dict[str, str] = {}
    if params.URL_PATH_IN_PROMPT:
        try:
            manifest_obj = await domains.dd.ingestion.storage.service.read_framework_manifest(blobs, slug)
            paths = domain.url_paths((manifest_obj or {}).get("entries") or [])
        except Exception as error:
            logger.info(f"[chapter_assign] {slug}: manifest unavailable, no URL paths for the scorer ({type(error).__name__})")

    digests = {
        key: shared.service.digest(
            versions.PROMPT_VERSION, proposals_fp, key, json.dumps(distillates.get(key) or {}, sort_keys = True), paths.get(key, ""),
        )
        for key in relevant_files
    }
    items = await domains.dd.pipeline.storage.service.read_items(blobs, slug, STEP, versions.PROMPT_VERSION, list(digests.values()))
    cached_items = {key: items[digest]["scores"] for key, digest in digests.items() if digest in items}
    todo = [key for key in relevant_files if key not in cached_items]
    if todo:
        await shared.service.settle(STEP, params.SETTLE_DELAY_S)

    # The scorer carries this run's chapters in its system prompt, so it is built once, here, for all the page branches.
    fixed_keys = params.FIXED_KEY_OUTPUT and len(proposals) >= params.FIXED_KEY_MIN_CHAPTERS
    context.agents.page_scorer(proposals, fixed_keys = fixed_keys)
    context.scratch[STEP] = {
        "proposals": proposals,
        "distillates": distillates,
        "fixed_keys": fixed_keys,
        "sweep_slots": asyncio.Semaphore(params.SWEEP_CONCURRENCY),
    }
    context.counters[STEP] = {"scored": 0}
    logger.info(
        f"[chapter_assign] {slug}: {len(relevant_files)} pages × {len(proposals)} chapters "
        f"({len(cached_items)} scored by an earlier run, {len(todo)} to score; fixed keys: {fixed_keys})"
    )
    return {
        "finished": False,
        "manifest_hash": manifest,
        "n_proposals": len(proposals),
        "digests": digests,
        "paths": paths,
        "cached": cached_items,
        "todo": todo,
        "swept": False,
        "sweep": [],
        "started": started,
    }


async def _ask(context: Any, data: dict, payload: str, meta: dict) -> Any:
    """One request to the scorer, returned as a `DocAssignment`."""
    schemas = agents.pipeline.planner.schemas
    n = len(data["proposals"])
    role = context.agents.page_scorer(data["proposals"], fixed_keys = data["fixed_keys"])
    schema = schemas.fixed_key_model(n) if data["fixed_keys"] else schemas.DocAssignment
    answer = await agents.service.ask(
        role, schema, payload, attempts = params.ASK_ATTEMPTS, wait_base_s = params.RETRY_WAIT_S, meta = meta,
    )
    return schemas.scores_from_fixed(answer, n) if data["fixed_keys"] else answer


def _report_quality(meta: dict, scores: list[dict], closest: bool) -> None:
    """Tell the Rotator which arm gave an empty answer (langfuse run 14: all 54 empty vectors came from one arm) and which one
    rescued it on the re-ask, so its bandit stops sending this task there."""
    try:
        if _best(scores) < params.RESCUE_FLOOR:
            domains.settings.chat.service.report_quality(meta, params.FEEDBACK_EMPTY_QUALITY)
        elif closest and _best(scores) >= params.CONFIDENCE_THRESHOLD:
            domains.settings.chat.service.report_quality(meta, params.FEEDBACK_RESCUE_QUALITY)
    except Exception:
        pass


async def _ladder(context: Any, data: dict, request: dict) -> tuple[list[dict] | None, bool]:
    """Score one page: (scores, used_fallback). `scores` is None only for a page with no content at all."""
    proposals = data["proposals"]
    n = len(proposals)
    base = domain.score_payload(**request)
    meta: dict = {}
    scores: list[dict] | None = None
    try:
        assignment = await _ask(context, data, base, meta)
    except Exception as error:
        assignment = None
        for _ in range(params.TRANSIENT_RETRIES):
            if domains.settings.chat.domain.classify_error(error) not in params.TRANSIENT_KINDS:
                break
            await asyncio.sleep(params.TRANSIENT_RETRY_PAUSE_S)
            try:
                assignment = await _ask(context, data, base, meta)
                break
            except Exception as again:
                error = again
        if assignment is None:
            logger.warning(f"[chapter_assign] {request['source_key']}: scoring failed ({type(error).__name__}: {error})")

    if assignment is not None:
        scores = _valid_scores(assignment, n)
        _report_quality(meta, scores, closest = False)
        missing = domain.missing_chapter_idxs(scores, n)
        if missing and domain.can_skip_reask(scores, missing):
            missing = []                                  # the model dropped one chapter; its best home is already confident
        if missing:
            # The model scored only some chapters (a short prefix-like list). Ask for the MISSING ones only and merge; a failed
            # ask keeps the partial vector.
            try:
                again = await _ask(
                    context, data,
                    domain.score_payload(**request, note = domain.missing_note(n - len(missing), n, missing)),
                    {},
                )
                have = {s["chapter_idx"] for s in scores}
                scores += [s for s in _valid_scores(again, n) if s["chapter_idx"] in missing and s["chapter_idx"] not in have]
            except Exception as error:
                logger.info(f"[chapter_assign] {request['source_key']}: completeness reask failed ({type(error).__name__}); partial vector kept")

    # An all-zero vector is an answer without content, not a verdict: ask again (the Rotator may hand the call to another arm) with
    # the "name the closest chapter" line; a genuine no-fit page stays zero and goes on to the leftover rules.
    for _ in range(params.ZERO_REASKS if scores is not None else 0):
        if _best(scores) >= params.RESCUE_FLOOR:
            break
        again_meta: dict = {}
        try:
            again = _valid_scores(
                await _ask(context, data, domain.score_payload(**request, note = domain.CLOSEST_CHAPTER), again_meta), n,
            )
        except Exception as error:
            logger.info(f"[chapter_assign] {request['source_key']}: all-zero reask failed ({type(error).__name__})")
            break
        _report_quality(again_meta, again, closest = True)
        if _best(again) > _best(scores):
            scores = again

    if scores is None:
        scores = domain.fallback_assign_scores(request["summary"], request["terms"], proposals, request["source_key"])
        used_fallback = bool(scores)
        if used_fallback:
            logger.info(f"[chapter_assign] {request['source_key']}: scoring failed — lexical fallback → chapter {scores[0]['chapter_idx']} (doc kept)")
        return scores, used_fallback
    return scores, False


async def score_page(state: graphs.pipeline.planner.chapter_assign.state.ScoreInput, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    data = context.scratch[STEP]
    key = state["key"]
    distillate = data["distillates"].get(key) or {}
    summary = (distillate.get("summary") or "").strip()
    body = ""
    if not summary:
        try:
            body = await context.blobs.read_text(key)
        except Exception:
            body = ""
    scores: list[dict] | None
    if not summary and not body.strip():
        scores, used_fallback = None, False
    else:
        request = {
            "framework": state["framework_slug"], "source_key": key, "doc_path": state["doc_path"],
            "summary": summary, "terms": distillate.get("key_terms") or [], "body": body,
        }
        if state["round"] == 2:
            async with data["sweep_slots"]:
                scores, used_fallback = await _ladder(context, data, request)
        else:
            scores, used_fallback = await _ladder(context, data, request)
        if scores is not None and not used_fallback:
            try:
                await domains.dd.pipeline.storage.service.write_item(
                    context.blobs, state["framework_slug"], STEP, versions.PROMPT_VERSION, state["digest"], {"scores": scores},
                )
            except Exception as error:
                logger.warning(f"[chapter_assign] scores not stored ({type(error).__name__}: {error})")

    if state["round"] == 1:
        counts = context.counters.setdefault(STEP, {"scored": 0})
        counts["scored"] += 1
        every = max(1, state["total"] // params.PROGRESS_EVENTS)
        if counts["scored"] % every == 0 or counts["scored"] == state["total"]:
            shared.service.emit(STEP, "llm_progress", scored = counts["scored"], total = state["total"])
    return {"results": [{"key": key, "scores": scores, "fallback": used_fallback, "round": state["round"]}]}


def tally(state: graphs.pipeline.planner.chapter_assign.state.AssignState, runtime: Runtime[Context]) -> dict:
    """After the main pass: the pages that ended on the lexical fallback get one more pass, unless too many failed (the Rotator is
    down and a sweep would only burn the time)."""
    if state.get("swept") or not params.SWEEP_ENABLED:
        return {"sweep": []}
    failed = sorted({r["key"] for r in state.get("results") or [] if r["fallback"]})
    n_docs = len(state.get("digests") or {})
    if not failed or len(failed) > params.SWEEP_MAX_SHARE * max(n_docs, 1):
        return {"sweep": [], "swept": True}
    return {"sweep": failed, "swept": True}


async def finalize(state: graphs.pipeline.planner.chapter_assign.state.AssignState, runtime: Runtime[Context]) -> dict:
    blobs = runtime.context.blobs
    slug = state["framework_slug"]
    relevant_files = state.get("relevant_files") or state.get("raw_files") or []
    n_proposals = state["n_proposals"]
    manifest = state["manifest_hash"]

    # A page's final result: the sweep's when it recovered it, else the first fallback.
    best: dict[str, dict] = {}
    for result in state.get("results") or []:
        previous = best.get(result["key"])
        if previous is None or (previous["fallback"] and not result["fallback"]):
            best[result["key"]] = result
    n_swept = len({r["key"] for r in state.get("results") or [] if r["round"] == 2})
    n_sweep_recovered = sum(1 for r in best.values() if r["round"] == 2 and not r["fallback"])
    if n_swept:
        logger.info(f"[chapter_assign] {slug}: fallback sweep recovered {n_sweep_recovered}/{n_swept} doc(s)")

    assignments: dict[str, list[dict]] = {key: scores for key, scores in (state.get("cached") or {}).items()}
    n_failed = 0
    fallbacks: list[str] = []
    for key in relevant_files:
        if key in assignments:
            continue
        result = best.get(key)
        if result is None or result["scores"] is None:
            n_failed += 1
            continue
        assignments[key] = result["scores"]
        if result["fallback"]:
            fallbacks.append(key)

    # RESCUE PASS: docs in [RESCUE_FLOOR, CONFIDENCE_THRESHOLD) get their best score floored to the threshold so they aren't silently dropped at chapter_select.
    rescued = domain.apply_rescue_pass(assignments)
    if rescued:
        logger.warning(
            f"[chapter_assign] {slug}: rescued {len(rescued)} doc(s) from the [{params.RESCUE_FLOOR}, {params.CONFIDENCE_THRESHOLD}) "
            f"confidence band — floored their best score to {params.CONFIDENCE_THRESHOLD}. Sample: "
            f"{[(r['key'], r['original_conf']) for r in rescued[:10]]}"
        )
    coverage_count = domain.compute_coverage_count(assignments, n_proposals)
    if fallbacks:
        logger.warning(f"[chapter_assign] {slug}: {len(fallbacks)} doc(s) used a lexical fallback assignment (scoring failed but doc kept): {fallbacks[:20]}")

    lkey = domains.dd.pipeline.storage.keys.assignments_latest_key(slug)
    await domains.dd.pipeline.storage.service.write_blob(
        blobs,
        domains.dd.pipeline.storage.keys.assignments_versioned_key(slug, manifest),
        lkey,
        {
            "prompt_version":    versions.PROMPT_VERSION,
            "framework_slug":    slug,
            "manifest_hash":     manifest,
            "assignments":       assignments,
            "n_docs":            len(relevant_files),
            "n_assigned":        len(assignments),
            "n_failed":          n_failed,
            "n_fallback":        len(fallbacks),
            "fallbacks":         fallbacks[:20],
            "n_swept":           n_swept,
            "n_sweep_recovered": n_sweep_recovered,
            "n_rescued":         len(rescued),
            "rescued":           rescued[:20],
            "n_proposals":       n_proposals,
            "coverage_count":    coverage_count,
            "confidence_thresh": params.CONFIDENCE_THRESHOLD,
            "rescue_floor":      params.RESCUE_FLOOR,
        },
    )
    wall_ms = shared.service.elapsed_ms(state.get("started") or time.monotonic())
    stats = {
        "n_docs": len(relevant_files),
        "n_assigned": len(assignments),
        "n_failed": n_failed,
        "n_fallback": len(fallbacks),
        "n_swept": n_swept,
        "n_sweep_recovered": n_sweep_recovered,
        "n_rescued": len(rescued),
        "n_proposals": n_proposals,
        "coverage_count": coverage_count,
        "cache_hit": False,
        "wall_ms": wall_ms,
        "manifest_hash": manifest,
        "n_cached": len(state.get("cached") or {}),
    }
    shared.service.emit(
        STEP, "done",
        cache_hit = False, n_assigned = len(assignments), n_failed = n_failed, n_fallback = len(fallbacks),
        n_rescued = len(rescued), wall_ms = wall_ms,
    )
    return {"chapter_doc_assignments_ref": lkey, "assign_stats": stats}
