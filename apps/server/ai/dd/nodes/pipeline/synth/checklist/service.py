"""checklist_eval steps — the nodes of its subgraph: prepare (the deterministic gates), judge (the chapter_judge agent), judge_again, majority, finalize.

The chapter is scored on 11 deterministic gates plus the five LLM criteria of the judge. The judge is an agent answering all five at once; a judge that fails
(almost) every criterion, or whose verdict alone would send the chapter back to the writer, is checked by two more runs that are graph branches, and the
per-criterion majority stands. A judge that never answers leaves the chapter scored on its gates and does not send it back for the outage. The
best-seen iteration (every gate passing first, then the score) is tracked here, so the render step ships it even when the loop stops on a worse one."""
from __future__ import annotations
import domains
from . import domain, keys, params, schemas, versions
from .. import sawc as sawc_stage
from .. import shared
from ..... import agents, graphs

import asyncio
import json
import logging
import time

from pydantic import ValidationError
from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)

Context = graphs.pipeline.synth.context.SynthContext
STEP = "checklist_eval"


def emit_criterion_scores(framework: str, criteria: list[dict | schemas.CriterionResult]) -> None:
    for criterion in criteria:
        name = criterion.name if isinstance(criterion, schemas.CriterionResult) else str((criterion or {}).get("name") or "")
        passed = criterion.passed if isinstance(criterion, schemas.CriterionResult) else bool((criterion or {}).get("passed", False))
        if name:
            domains.dd.pipeline.observability.service.record_grader_dim_score(framework = framework, dim = name, score = 1.0 if passed else 0.0)


def unavailable(results: list[schemas.CriterionResult]) -> bool:
    return any((r.feedback or "").startswith("judge_unavailable") for r in results)


async def run_judge(context: Context, frame: dict) -> dict:
    """One judge run. Returns a `JudgeRun`: the five verdicts, whether the judge answered, and the Rotator's meta for the call.

    A call that fails for infrastructure reasons is asked again (a lighter chapter after the first failure), with a cool-off and a total time budget; an answer the
    judge gave that is not a valid verdict set is a content failure and is not retried. A FAIL with no reason cannot be acted on: it is asked once more and, if still
    unexplained, counted as passed rather than rewriting a chapter for nothing."""
    t0 = time.monotonic()
    chapter, grounding, truncated = frame["chapter"], frame["grounding"], frame["truncated"]
    n_attempts = params.MAX_CALL_ATTEMPTS + params.JUDGE_EXTRA_ATTEMPTS
    meta: dict = {}
    payload: schemas.LLMJudgePayload | None = None
    last_error: Exception | None = None
    content_error: str | None = None
    for attempt in range(n_attempts):
        request = domain.judge_request(
            chapter_id = frame["chapter_id"], chapter_title = frame["chapter_title"], framework = frame["framework"],
            rendered_chapter = chapter, rendered_digest = grounding, truncated = truncated,
        )
        try:
            answer = await agents.service.ask(
                context.agents.chapter_judge(), agents.pipeline.synth.schemas.JudgeAnswer, json.dumps(request, ensure_ascii = False),
                attempts = params.ASK_ATTEMPTS, wait_base_s = params.RETRY_WAIT_S, meta = meta,
            )
            payload = schemas.LLMJudgePayload.model_validate(answer.model_dump())
            last_error = None
            break
        except (ValidationError, ValueError) as error:
            # Content, not infrastructure: the judge answered, badly. The retry loop below is for infrastructure only.
            content_error = str(error)[:300]
            last_error = None
            break
        except Exception as error:
            last_error = error
            if attempt < n_attempts - 1:
                if (time.monotonic() - t0) + params.JUDGE_COOLOFF_S + params.JUDGE_RUN_BUDGET_S > params.JUDGE_TOTAL_BUDGET_S:
                    logger.warning(f"[checklist_eval] LLM judge: {int(time.monotonic() - t0)} s spent over {attempt + 1} attempt(s) — the next cannot finish inside the {params.JUDGE_TOTAL_BUDGET_S:.0f} s budget; giving up")
                    break
                # A smaller prompt finishes more often on a congested pool: the first failure of any kind re-renders a lighter chapter.
                if domain.is_context_overflow_error(error) or attempt == 0:
                    chapter, truncated = domain.render_chapter_for_judge(frame["sawc"], char_cap = params.MAX_RENDERED_CHAPTER_CHARS // 2)
                    grounding = domain.render_digest_for_grounding(frame["digest"], char_cap = 10_000)
                await asyncio.sleep(params.JUDGE_COOLOFF_S if attempt == params.MAX_CALL_ATTEMPTS - 1 else 1.0)
    wall_ms = shared.service.elapsed_ms(t0)
    if last_error is not None:
        logger.warning(f"[checklist_eval] LLM judge call failed after {n_attempts} attempt(s): {type(last_error).__name__}: {last_error}")
        return {"results": [r.model_dump() for r in domain.fallback_llm_verdicts(type(last_error).__name__)], "answered": False, "repaired": False, "wall_ms": wall_ms, "meta": {}}
    if payload is None:
        logger.warning(f"[checklist_eval] LLM judge unparseable ({content_error}); using fallback FAIL verdicts")
        return {"results": [r.model_dump() for r in domain.fallback_llm_verdicts(f"judge_parse_failed: {content_error}")], "answered": False, "repaired": False, "wall_ms": wall_ms, "meta": {}}

    results = domain.llm_payload_to_criteria(payload)
    unexplained = [r.name for r in results if not r.passed and domain.is_placeholder_feedback(r.feedback)]
    if unexplained:
        try:
            again = await agents.service.ask(
                context.agents.chapter_judge(), agents.pipeline.synth.schemas.JudgeAnswer,
                json.dumps(domain.judge_request(
                    chapter_id = frame["chapter_id"], chapter_title = frame["chapter_title"], framework = frame["framework"],
                    rendered_chapter = chapter, rendered_digest = grounding, truncated = truncated,
                ), ensure_ascii = False),
                attempts = params.ASK_ATTEMPTS, wait_base_s = params.RETRY_WAIT_S,
            )
            second = {r.name: r for r in domain.llm_payload_to_criteria(schemas.LLMJudgePayload.model_validate(again.model_dump()))}
            results = [second[r.name] if r.name in unexplained and r.name in second else r for r in results]
        except Exception as error:
            logger.warning(f"[checklist_eval] judge re-ask for unexplained FAIL(s) failed: {type(error).__name__}: {error}")
        results = [
            r.model_copy(update = {"passed": True, "feedback": ""}) if (not r.passed and domain.is_placeholder_feedback(r.feedback)) else r
            for r in results
        ]
    return {"results": [r.model_dump() for r in results], "answered": True, "repaired": False, "wall_ms": wall_ms, "meta": meta}


async def prepare(state: graphs.pipeline.synth.checklist.state.ChecklistState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    started = time.monotonic()
    if not slug or not chapter_id:
        return {"finished": True, **shared.service.failure(
            "checklist_path", "checklist_stats", "no_slug_or_chapter_id", "framework_slug or chapter_id missing from SynthState", started,
        )}

    sawc_key = keys.sawc_latest_key(slug, chapter_id)
    digest_key = keys.digest_latest_key(slug, chapter_id)
    if not await blobs.exists(sawc_key):
        return {"finished": True, **shared.service.failure(
            "checklist_path", "checklist_stats", "sawc_not_found", f"sawc {sawc_key!r} not in MinIO — run sawc_write first", started, sawc_key = sawc_key,
        )}
    if not await blobs.exists(digest_key):
        return {"finished": True, **shared.service.failure(
            "checklist_path", "checklist_stats", "digest_not_found", f"digest {digest_key!r} not in MinIO — run digest_construct first", started, digest_key = digest_key,
        )}
    sawc = await shared.service.load_json(blobs, sawc_key)
    digest = await shared.service.load_json(blobs, digest_key)
    if sawc is None or digest is None:
        return {"finished": True, **shared.service.failure("checklist_path", "checklist_stats", "inputs_unreadable", "sawc/digest unreadable", started)}

    chapter_title = sawc.get("chapter_title") or chapter_id
    sawc_manifest_hash = sawc.get("sawc_manifest_hash") or ""
    digest_manifest_hash = digest.get("digest_manifest_hash") or ""
    shared.service.emit(
        STEP, "start",
        chapter_id = chapter_id, chapter_title = chapter_title, n_total_criteria = len(domain.DETERMINISTIC_CHECKS) + len(params.LLM_CRITERIA), pass_threshold = params.PASS_THRESHOLD,
    )
    manifest_hash = domain.compute_manifest_hash(sawc_manifest_hash = sawc_manifest_hash, digest_manifest_hash = digest_manifest_hash)
    versioned_key = keys.versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key = keys.latest_blob_key(slug, chapter_id)

    cache_usable = await blobs.exists(versioned_key) and await blobs.exists(latest_key)
    if cache_usable:
        # A verdict scored on the gates alone because the judge was down is not final: judging again is the only way to ever get the LLM review.
        try:
            if json.loads(await blobs.read_text(versioned_key)).get("judge_unavailable"):
                logger.info(f"[checklist_eval] {slug}/{chapter_id}: the cached verdict has no LLM judge (outage) — judging again")
                cache_usable = False
        except Exception:
            pass
    if cache_usable:
        try:
            cached = json.loads(await blobs.read_text(versioned_key))
            elapsed = shared.service.elapsed_ms(started)
            stats = {
                "n_total": cached.get("n_total", 0),
                "n_passed": cached.get("n_passed", 0),
                "pass_rate": cached.get("pass_rate", 0.0),
                "chapter_passed": cached.get("chapter_passed", False),
                "n_failed_feedback": len(cached.get("failed_feedback") or []),
                "failed_feedback": cached.get("failed_feedback") or [],
                "wall_ms": elapsed,
                "store_path": latest_key,
                "versioned_path": versioned_key,
                "manifest_hash": manifest_hash,
                "cache_hit": True,
                "prompt_version": cached.get("prompt_version"),
                "infra_degraded": False,   # a cached result is a completed prior run
            }
            shared.service.emit(
                STEP, "done",
                n_total = stats["n_total"], n_passed = stats["n_passed"], pass_rate = stats["pass_rate"], chapter_passed = stats["chapter_passed"],
                n_failed_feedback = stats["n_failed_feedback"], wall_ms = elapsed, cache_hit = True,
            )
            logger.info(f"[checklist_eval] {slug}/{chapter_id}: CACHE HIT — {stats['n_passed']}/{stats['n_total']} ({stats['pass_rate']:.0%}), passed = {stats['chapter_passed']}, {elapsed} ms")
            emit_criterion_scores(slug, cached.get("criteria") or [])
            # The same immediate best-seen promotion as a fresh evaluation: a cache hit is still this iteration's real score.
            n_pregate_passed = sum(1 for c in (cached.get("criteria") or []) if c.get("kind") == "deterministic" and c.get("passed"))
            incoming_score, incoming_pregate = state.get("best_seen_score"), state.get("best_seen_pregate")
            patch = {
                "finished": True, "checklist_path": latest_key, "checklist_stats": stats,
                # A cache hit is a completed prior run, not a live outage — reset the streak.
                "consecutive_infra_degraded": 0,
                "best_seen_score": incoming_score, "best_seen_pregate": incoming_pregate, "best_seen_sawc_path": state.get("best_seen_sawc_path"),
            }
            if incoming_score is None or domain.best_seen_key(stats["pass_rate"], n_pregate_passed) > domain.best_seen_key(incoming_score, incoming_pregate or 0):
                patch.update({
                    "best_seen_score": stats["pass_rate"], "best_seen_pregate": n_pregate_passed,
                    "best_seen_sawc_path": sawc_stage.keys.versioned_blob_key(slug, chapter_id, sawc_manifest_hash),
                })
            return patch
        except Exception as error:
            logger.warning(f"[checklist_eval] {slug}/{chapter_id}: cached blob {versioned_key!r} unreadable ({type(error).__name__}: {error}); recomputing")

    # Signals for the gates only (not persisted): does the chapter's source material contain ANY code block, which blocks were routed, their bodies.
    n_vault = digest.get("n_total_vault_hashes")
    used_hashes = {st.get("code_ref_hash") for s in (sawc.get("sections") or []) for st in (s.get("subtopics") or []) if (st or {}).get("code_ref_hash")}
    routed = {h for cs in (digest.get("per_section") or {}).values() for c in cs for h in (c.get("code_refs") or [])}
    vault_bodies: dict[str, str] = {}
    if used_hashes:
        try:
            rich, _, _ = await shared.service.load_vault_rich(blobs, slug, list(digest.get("source_keys") or []))
            vault_bodies = {h: e.fence_text for h, e in rich.items() if h in (used_hashes | routed) and getattr(e, "fence_text", "")}
        except Exception as error:
            logger.info(f"[checklist_eval] {slug}/{chapter_id}: vault bodies unavailable for the alignment gate ({type(error).__name__})")
    for_checks = {
        **sawc,
        "_chapter_has_code": (int(n_vault) > 0) if isinstance(n_vault, (int, float)) else None,
        "_routed_hashes": sorted(routed),
        "_digest_sources": list(digest.get("source_keys") or []),
        "_vault_bodies": vault_bodies,
    }
    pre: list[schemas.CriterionResult] = []
    for check in domain.DETERMINISTIC_CHECKS:
        try:
            pre.append(check(for_checks))
        except Exception as error:
            logger.warning(f"[checklist_eval] pre-gate {check.__name__} crashed: {type(error).__name__}: {error}")
            pre.append(schemas.CriterionResult(
                name = check.__name__.replace("check_", ""), passed = False, kind = "deterministic", feedback = f"pre_gate_crashed: {type(error).__name__}",
            ))
    shared.service.emit(
        STEP, "pregates_done", n_pregate = len(pre), n_passed = sum(1 for r in pre if r.passed), names_failed = [r.name for r in pre if not r.passed],
    )
    chapter, truncated = domain.render_chapter_for_judge(sawc)
    grounding = domain.render_digest_for_grounding(digest)
    shared.service.emit(STEP, "judge_request", chapter_chars = len(chapter), digest_chars = len(grounding), truncated = truncated)
    context.scratch[STEP] = {
        "frame": {
            "chapter_id": chapter_id, "chapter_title": chapter_title, "framework": slug, "sawc": sawc, "digest": digest,
            "chapter": chapter, "grounding": grounding, "truncated": truncated,
        },
    }
    return {
        "finished": False,
        "started": started,
        "manifest_hash": manifest_hash,
        "versioned_key": versioned_key,
        "latest_key": latest_key,
        "chapter_title": chapter_title,
        "sawc_manifest_hash": sawc_manifest_hash,
        "digest_manifest_hash": digest_manifest_hash,
        "pre_results": [r.model_dump() for r in pre],
    }


async def judge(state: graphs.pipeline.synth.checklist.state.ChecklistState, runtime: Runtime[Context]) -> dict:
    run = await run_judge(runtime.context, runtime.context.scratch[STEP]["frame"])
    results = run["results"]
    shared.service.emit(
        STEP, "judge_done",
        n_llm = len(results), n_passed = sum(1 for r in results if r["passed"]), names_failed = [r["name"] for r in results if not r["passed"]],
        wall_ms = run["wall_ms"], answered = run["answered"],
    )
    return {"judge": run}


async def judge_again(state: dict, runtime: Runtime[Context]) -> dict:
    """A further opinion on a chapter the first judge failed (the Rotator may route it to another model)."""
    return {"opinions": [await run_judge(runtime.context, runtime.context.scratch[STEP]["frame"])]}


async def majority(state: graphs.pipeline.synth.checklist.state.ChecklistState, runtime: Runtime[Context]) -> dict:
    first = [schemas.CriterionResult.model_validate(r) for r in state["judge"]["results"]]
    runs = [first] + [
        [schemas.CriterionResult.model_validate(r) for r in o["results"]] for o in state.get("opinions") or [] if o["answered"] and not unavailable([schemas.CriterionResult.model_validate(r) for r in o["results"]])
    ]
    if len(runs) < 2:
        return {"merged": None}
    merged = domain.majority_verdicts(runs)
    logger.info(
        f"[checklist_eval] {state['framework_slug']}/{state['chapter_id']}: first judge failed {sum(1 for r in first if not r.passed)}/{len(first)}; "
        f"majority of {len(runs)} judge runs fails {sum(1 for r in merged if not r.passed)}"
    )
    return {"merged": [r.model_dump() for r in merged]}


async def finalize(state: graphs.pipeline.synth.checklist.state.ChecklistState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    slug, chapter_id = state["framework_slug"], state["chapter_id"]
    run = state["judge"]
    pre = [schemas.CriterionResult.model_validate(r) for r in state["pre_results"]]
    llm = [schemas.CriterionResult.model_validate(r) for r in (state.get("merged") or run["results"])]
    n_pre_passed = sum(1 for r in pre if r.passed)
    llm_failed = [r.name for r in llm if not r.passed]
    # An all-pass judge on a chapter the deterministic gates reject is a lenient judge — tell the Rotator so its bandit demotes that arm for judging.
    try:
        if llm and not llm_failed and any(not r.passed and r.name in params.JUDGE_CONTRADICTED_BY for r in pre):
            domains.settings.chat.service.report_quality(run["meta"], params.FEEDBACK_LENIENT_JUDGE_QUALITY)
    except Exception:
        pass

    judge_unavailable = bool(llm) and unavailable(llm)
    if judge_unavailable:
        logger.warning(
            f"[checklist_eval] {slug}/{chapter_id}: judge unavailable after retries; the chapter is scored on its {len(pre)} deterministic gates and is not sent back to the writer for the outage"
        )
    all_results = list(pre) + ([] if judge_unavailable else list(llm))
    n_passed, n_total, pass_rate, chapter_passed = domain.aggregate_pass_rate(all_results)
    failed_feedback = domain.collect_failed_feedback(all_results)

    # Best-seen promotion happens HERE, immediately: the current iteration's own fresh score must be in state before the router decides to loop. Ordering: every
    # deterministic gate passing first, then the score, then the gate count (a tie on pass_rate can be a judge failure erasing a real structural advantage).
    sawc_manifest_hash = state["sawc_manifest_hash"]
    incoming_score, incoming_pregate = state.get("best_seen_score"), state.get("best_seen_pregate")
    best_score, best_pregate, best_path = incoming_score, incoming_pregate, state.get("best_seen_sawc_path")
    is_better = incoming_score is None or domain.best_seen_key(pass_rate, n_pre_passed) > domain.best_seen_key(incoming_score, incoming_pregate or 0)
    if is_better:
        best_score, best_pregate = pass_rate, n_pre_passed
        best_path = sawc_stage.keys.versioned_blob_key(slug, chapter_id, sawc_manifest_hash)

    answered = bool(run["answered"])
    evaluation = schemas.ChecklistEvaluation(
        chapter_id = chapter_id,
        chapter_title = state["chapter_title"],
        framework_slug = slug,
        criteria = all_results,
        n_passed = n_passed,
        n_total = n_total,
        pass_rate = pass_rate,
        chapter_passed = chapter_passed,
        failed_feedback = failed_feedback,
        n_llm_judge_repairs = 0,
        judge_unavailable = judge_unavailable,
        wall_ms = shared.service.elapsed_ms(state["started"]),
    )
    payload = evaluation.model_dump()
    payload["sawc_manifest_hash"] = sawc_manifest_hash
    payload["digest_manifest_hash"] = state["digest_manifest_hash"]
    payload["checklist_manifest_hash"] = state["manifest_hash"]
    body = json.dumps(payload, indent = 2, ensure_ascii = False)
    logger.info(f"[checklist_eval] {slug}/{chapter_id}: persisting evaluation ({len(body)} bytes) to MinIO")
    try:
        writes = [(state["versioned_key"], body), (state["latest_key"], body)] + ([(keys.best_blob_key(slug, chapter_id), body)] if is_better else [])
        for key, content in writes:
            await asyncio.wait_for(context.blobs.write(key, content, content_type = "application/json"), timeout = params.TIMEOUT_S_PERSIST_WRITE)
    except asyncio.TimeoutError:
        logger.warning(f"[checklist_eval] {slug}/{chapter_id}: evaluation persist timed out after {params.TIMEOUT_S_PERSIST_WRITE}s — MinIO likely degraded; re-raising")
        raise

    elapsed = shared.service.elapsed_ms(state["started"])
    # A low pass rate driven by the judge infrastructure itself failing (not by the judge reviewing the chapter and rejecting it) is a different signal than
    # genuine content quality; so is a writer whose runs failed for infrastructure reasons (anything in the sawc error breakdown but a model-output-quality issue).
    sawc_breakdown = (state.get("sawc_stats") or {}).get("error_breakdown") or {}
    writer_degraded = any(kind not in ("parse_failed", "pydantic_fail") for kind in sawc_breakdown)
    infra_degraded = (not answered) or writer_degraded
    # Sustained-outage streak: iterations IN A ROW, ending with this one, that were infra-degraded; the router halts early once it crosses a threshold.
    consecutive = int(state.get("consecutive_infra_degraded") or 0) + 1 if infra_degraded else 0
    stats = {
        "n_total": n_total,
        "n_passed": n_passed,
        "pass_rate": pass_rate,
        "chapter_passed": chapter_passed,
        "n_failed_feedback": len(failed_feedback),
        # The actual strings, not just the count: the next iteration's reviewer and writer read them.
        "failed_feedback": failed_feedback,
        "n_pregate_passed": n_pre_passed,
        "n_pregate_total": len(pre),
        "n_llm_passed": sum(1 for r in llm if r.passed),
        "n_llm_total": len(llm),
        "names_failed": [r.name for r in all_results if not r.passed],
        "judge_wall_ms": run["wall_ms"],
        "judge_repaired": False,
        "judge_call_failed": not answered,
        "infra_degraded": infra_degraded,
        "sawc_writer_degraded": writer_degraded,
        "n_judge_runs": 1 + len(state.get("opinions") or []),
        "wall_ms": elapsed,
        "store_path": state["latest_key"],
        "versioned_path": state["versioned_key"],
        "manifest_hash": state["manifest_hash"],
        "cache_hit": False,
        "prompt_version": versions.CHECKLIST_PROMPT_VERSION,
    }
    shared.service.emit(
        STEP, "done",
        n_total = n_total, n_passed = n_passed, pass_rate = pass_rate, chapter_passed = chapter_passed, n_failed_feedback = len(failed_feedback), wall_ms = elapsed,
    )
    logger.info(
        f"[checklist_eval] {slug}/{chapter_id}: {n_passed}/{n_total} criteria passed ({pass_rate:.0%}, threshold {params.PASS_THRESHOLD:.0%}, chapter_passed = {chapter_passed}); "
        f"pre = {n_pre_passed}/{len(pre)}, llm = {stats['n_llm_passed']}/{len(llm)}; {len(failed_feedback)} feedback strings; judge_wall = {run['wall_ms']}ms, total = {elapsed}ms"
    )
    emit_criterion_scores(slug, all_results)
    return {
        "checklist_path": state["latest_key"],
        "checklist_stats": stats,
        # Top-level (not nested in checklist_stats): the router reads SynthState fields directly.
        "consecutive_infra_degraded": consecutive,
        "best_seen_score": best_score,
        "best_seen_pregate": best_pregate,
        "best_seen_sawc_path": best_path,
    }
