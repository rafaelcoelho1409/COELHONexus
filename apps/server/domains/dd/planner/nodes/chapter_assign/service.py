"""chapter_assign I/O shell — per-doc LLM scoring, latest-blob loader,
and the chapter_assign_run orchestration.

SOTA Sept 2026 on the rotator pooled:
- Pooled AsyncOpenAI http2 200/100 replaces per-call ChatOpenAI alloc.
- CONCURRENCY 12 (was 24 → 103 lexical fallback 75%; 12 cuts burst 50%).
- Bulk read_many for summary-miss docs outside semaphore (shared S3 client).
- Prompt static prefix (chapters rubric) before dynamic doc → KV-cache reuse.
"""
from __future__ import annotations
import domains, infra
from . import domain, keys, params, prompts, schemas, versions

import asyncio
import json
import logging
import time
from typing import Optional

from pydantic import ValidationError


logger = logging.getLogger(__name__)


async def _score_call(prompt: str, schema = None, meta_out: dict | None = None):
    """One LLM call, decoded via LangChain's `.with_structured_output()`.
    Raises ValueError (unparseable) or pydantic ValidationError (schema
    mismatch) for reask-eligible failures; anything else (timeout,
    provider outage) propagates as-is."""
    budget = params.MAX_TOKENS
    for attempt in range(2):
        try:
            assignment, meta = await domains.settings.chat.service.chat_structured_async(
                prompt,
                schema or schemas.DocAssignment,
                max_tokens = budget,
                temperature = params.TEMPERATURE,
                timeout_s = params.TIMEOUT_S,
                rotator_task = params.ROTATOR_TASK,
            )
            if meta_out is not None:
                meta_out.update(meta or {})
            return assignment
        except Exception as e:
            # Cut at max_tokens (a reasoning arm's preamble + a 24-entry score list on a huge
            # page): the budget was wrong, not the doc — one retry with a bigger one beats
            # the lexical fallback (fastmcp: 2 of 350 docs).
            if attempt == 0 and domains.settings.chat.domain.is_length_error(e):
                budget = min(params.MAX_TOKENS_RETRY_CAP, int(budget * params.LENGTH_RETRY_FACTOR))
                continue
            raise
    raise RuntimeError("unreachable")


async def _score_primary(prompt: str, n_proposals: int, closest: bool = False) -> schemas.DocAssignment:
    """The first scoring call: one required key per chapter when the plan is big enough (see
    params.FIXED_KEY_OUTPUT), else — or when that answer is malformed — the score list. `closest` swaps the
    honesty line for the "name the closest chapter" one (the re-ask of an all-zero answer)."""
    asked = prompts.closest_chapter if closest else (lambda p: p)
    if params.FIXED_KEY_OUTPUT and n_proposals >= params.FIXED_KEY_MIN_CHAPTERS:
        try:
            fixed_prompt = prompts.to_fixed_key_prompt(prompt, n_proposals)
        except ValueError:
            fixed_prompt = None                              # a template without the expected OUTPUT block
        if fixed_prompt:
            try:
                meta: dict = {}
                obj = await _score_call(asked(fixed_prompt), schemas.fixed_key_model(n_proposals), meta_out = meta)
                out = schemas.scores_from_fixed(obj, n_proposals)
                _report_vector_quality(meta, out, closest)
                return out
            except (ValidationError, ValueError) as e:
                logger.info(f"[chapter_assign] fixed-key answer rejected ({type(e).__name__}) — score list instead")
    meta = {}
    out = await _score_call(asked(prompt), meta_out = meta)
    _report_vector_quality(meta, out, closest)
    return out


def _report_vector_quality(meta: dict, assignment: schemas.DocAssignment, closest: bool) -> None:
    """Tell the Rotator which arm gave an empty answer (every score below RESCUE_FLOOR: langfuse run 14, all 54 came from
    nvidia/ising-calibration) and which one rescued it on the re-ask, so its bandit stops sending this task there."""
    try:
        best = max((sc.confidence for sc in assignment.scores), default = 0.0)
        if best < params.RESCUE_FLOOR:
            domains.settings.chat.service.report_quality(meta, params.FEEDBACK_EMPTY_QUALITY)
        elif closest and best >= params.CONFIDENCE_THRESHOLD:
            domains.settings.chat.service.report_quality(meta, params.FEEDBACK_RESCUE_QUALITY)
    except Exception:
        pass


def _valid_scores(assignment: schemas.DocAssignment, n_proposals: int) -> list[dict]:
    return [
        {"chapter_idx": sc.chapter_idx, "confidence": sc.confidence}
        for sc in assignment.scores
        if 0 <= sc.chapter_idx < n_proposals
    ]


async def assign_one(
    sem: asyncio.Semaphore,
    *args,
    **kwargs,
) -> tuple[str, Optional[list[dict]], int, bool]:
    """(source_key, scores, wall_ms, used_fallback). scores=None only when
    doc has no content; failed LLM with content gets lexical fallback.

    SOTA: doc_body pre-fetched outside semaphore (bulk read_many) so sem
    gates only the network-bound LLM hop (pooled http2 200/100).

    Supports both new (sem, framework, source_key, distillate, proposals,
    doc_body="") and legacy (sem, minio, framework, source_key, distillate,
    proposals) call shapes for backward compat.
    """
    # Parse args for backward compat
    doc_body = kwargs.get("doc_body", "")
    if len(args) == 5 and hasattr(args[0], "read_text"):
        # Legacy: (minio, framework, source_key, distillate, proposals)
        _minio_legacy, framework, source_key, distillate, proposals = args  # type: ignore
        # If doc_body not supplied, fallback to legacy I/O inside sem (slow path)
        if not doc_body and not ((distillate or {}).get("summary") or "").strip():
            try:
                doc_body = await _minio_legacy.read_text(source_key)  # type: ignore
            except Exception:
                doc_body = ""
    elif len(args) == 5:
        # New: (framework, source_key, distillate, proposals, doc_body) as positional 5th
        framework, source_key, distillate, proposals, doc_body = args  # type: ignore
    elif len(args) == 4:
        # New: (framework, source_key, distillate, proposals) + kw doc_body
        framework, source_key, distillate, proposals = args  # type: ignore
        doc_body = kwargs.get("doc_body", doc_body)
    else:
        raise TypeError(f"assign_one: unexpected args len {len(args)}")

    async with sem:
        t0 = time.monotonic()
        doc_summary = (distillate or {}).get("summary") or ""  # type: ignore
        doc_terms = (distillate or {}).get("key_terms") or []
        # doc_body supplied by caller for summary-miss docs; no I/O here.
        if not doc_summary and not (doc_body or "").strip():
            return (
                source_key, None,
                int((time.monotonic() - t0) * 1000),
                False,
            )

        prompt = prompts.build_prompt(
            framework = framework,
            source_key = source_key,
            doc_summary = doc_summary,
            doc_terms = doc_terms,
            doc_body = doc_body,
            proposals = proposals,
            doc_path = (kwargs.get("doc_path") or "") if params.URL_PATH_IN_PROMPT else "",
        )

        scores: Optional[list[dict]] = None
        assignment: Optional[schemas.DocAssignment] = None
        try:
            # dd-reduce-label = non-reasoning pool; <think> blocks waste 10-25s on JSON scoring.
            assignment = await _score_primary(prompt, len(proposals))
        except (ValidationError, ValueError) as e:
            # Reask once: the model responded but the output was malformed/invalid —
            # cheap self-repair by feeding the error back verbatim. Deliberately NOT
            # applied to the generic-Exception branch below (timeouts, provider
            # outages) — retrying a doomed call there just burns the concurrency
            # slot; the rotator's own circuit breaker is what fixes those upstream.
            logger.info(
                f"[chapter_assign] {source_key}: reask after "
                f"{type(e).__name__}: {str(e)[:160]}"
            )
            try:
                assignment = await _score_call(
                    f"{prompt}\n\nYour previous response was invalid: "
                    f"{str(e)[:300]}\nReturn ONLY corrected JSON matching the schema."
                )
            except Exception as e2:
                logger.warning(
                    f"[chapter_assign] LLM/parse/validate failed for "
                    f"{source_key} (after reask): {type(e2).__name__}: {e2}"
                )
        except Exception as e:
            logger.warning(
                f"[chapter_assign] LLM/parse/validate failed for "
                f"{source_key}: {type(e).__name__}: {e}"
            )
            for _retry in range(params.TRANSIENT_RETRIES):
                if domains.settings.chat.domain.classify_error(e) not in params.TRANSIENT_KINDS:
                    break
                await asyncio.sleep(params.TRANSIENT_RETRY_PAUSE_S)
                try:
                    assignment = await _score_call(prompt)
                    logger.info(f"[chapter_assign] {source_key}: transient retry recovered")
                    break
                except Exception as e2:
                    e = e2
                    logger.warning(
                        f"[chapter_assign] {source_key}: transient retry failed: "
                        f"{type(e2).__name__}: {e2}"
                    )

        if assignment is not None:
            n_proposals = len(proposals)
            scores = _valid_scores(assignment, n_proposals)
            missing = domain.missing_chapter_idxs(scores, n_proposals)
            if missing and domain.can_skip_reask(scores, missing):
                missing = []                    # the model dropped one chapter; its best home is already confident
            if missing:
                # The model scored only some chapters (~10% of docs on a 24-chapter plan; always a
                # short prefix-like list). Complete vectors — the norm — never reach this branch.
                # Reask for the MISSING chapters only (a third of the output of a full reask) and
                # merge; a failed reask keeps the partial vector.
                logger.info(
                    f"[chapter_assign] {source_key}: scored {n_proposals - len(missing)}"
                    f"/{n_proposals} chapters — reasking for the rest"
                )
                try:
                    again = await _score_call(
                        f"{prompt}\n\nYour previous response scored only "
                        f"{n_proposals - len(missing)} of the {n_proposals} chapters. Now return ONE "
                        f"entry for EACH of these chapter_idx values and no others: {missing}, with "
                        f"confidence 0.0 where the doc does not apply."
                    )
                    have = {s_["chapter_idx"] for s_ in scores}
                    extra = [
                        s_ for s_ in _valid_scores(again, n_proposals)
                        if s_["chapter_idx"] in missing and s_["chapter_idx"] not in have
                    ]
                    if extra:
                        scores = scores + extra
                except Exception as e:
                    logger.info(
                        f"[chapter_assign] {source_key}: completeness reask failed "
                        f"({type(e).__name__}) — keeping the partial vector"
                    )

        # An all-zero vector is an answer without content, not a verdict: re-ask (the Rotator may hand the call to another
        # arm) with the "name the closest chapter" line; a genuine no-fit page stays zero and goes on to the leftover rules.
        n_proposals = len(proposals)
        for _z in range(params.ZERO_REASKS if scores is not None else 0):
            if max((s_["confidence"] for s_ in scores), default = 0.0) >= params.RESCUE_FLOOR:
                break
            try:
                again = _valid_scores(await _score_primary(prompt, n_proposals, closest = True), n_proposals)
            except Exception as e:
                logger.info(f"[chapter_assign] {source_key}: all-zero reask failed ({type(e).__name__})")
                break
            if max((s_["confidence"] for s_ in again), default = 0.0) > max((s_["confidence"] for s_ in scores), default = 0.0):
                scores = again

        # Failed LLM (None) → lexical fallback so doc reaches chapter_select.
        # Successful but empty (LLM judged irrelevant) is left as-is.
        used_fallback = False
        if scores is None:
            scores = domain.fallback_assign_scores(
                doc_summary, doc_terms, proposals, source_key,
            )
            used_fallback = bool(scores)
            if used_fallback:
                logger.info(
                    f"[chapter_assign] {source_key}: assign failed — "
                    f"lexical fallback → chapter "
                    f"{scores[0]['chapter_idx']} (doc kept)"
                )
        return (
            source_key, scores,
            int((time.monotonic() - t0) * 1000),
            used_fallback,
        )


async def sweep_failed(results: list, make_task, n_docs: int, settle = None) -> tuple[list, int, int]:
    """One more pass over the docs that ended on the lexical fallback: `make_task(key, sem)` builds the
    assign_one coroutine. Returns (results with the recovered docs swapped in, n swept, n recovered); a doc that
    fails again keeps its first fallback result. Skipped when too many docs failed (Rotator down) and cut at
    SWEEP_BUDGET_S; `settle()` (awaitable factory) waits for the Rotator pool first."""
    keys_ = [r[0] for r in results if r[3]]
    if not params.SWEEP_ENABLED or not keys_ or len(keys_) > params.SWEEP_MAX_SHARE * max(n_docs, 1):
        return results, 0, 0
    if settle is not None:
        await settle()
    sem = asyncio.Semaphore(params.SWEEP_CONCURRENCY)
    tasks = [asyncio.ensure_future(make_task(k, sem)) for k in keys_]
    done, pending = await asyncio.wait(tasks, timeout = params.SWEEP_BUDGET_S)
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions = True)
    redo = {}
    for t in done:
        if t.cancelled() or t.exception() is not None:
            continue
        r = t.result()
        if r[1] and not r[3]:
            redo[r[0]] = r
    return [redo.get(r[0], r) for r in results], len(keys_), len(redo)


async def load_assignments(minio, slug: str) -> dict:
    """Returns {source_key: [{chapter_idx, confidence}, ...]}."""
    try:
        text = await minio.read_text(keys.latest_key(slug))
        data = json.loads(text)
        return data.get("assignments") or {}
    except Exception:
        return {}


async def chapter_assign_run(state: domains.dd.planner.state.PlannerState) -> dict:
    """Load proposals + distillates → score every doc via the bandit →
    persist {source_key: [{chapter_idx, confidence}]} matrix."""
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    relevant_files = (
        state.get("relevant_files") or state.get("raw_files") or []
    )
    proposals_ref = state.get("chapter_proposals_ref")

    if not slug or not relevant_files or not proposals_ref:
        return {
            "chapter_doc_assignments_ref": None,
            "assign_stats": {"skipped": "missing_inputs"},
        }

    t0 = time.monotonic()
    minio = domains.dd.ingestion.storage.service.get_storage()
    proposals_obj = await domains.dd.planner.nodes.chapter_propose.service.load_proposals(minio, slug)
    if proposals_obj is None or not proposals_obj.proposals:
        return {
            "chapter_doc_assignments_ref": None,
            "assign_stats": {"skipped": "no_proposals_loaded"},
        }
    proposals_dicts = [p.model_dump() for p in proposals_obj.proposals]
    distillates = await domains.dd.planner.nodes.doc_distill.service.load_distillates(minio, slug)

    manifest = domain.manifest_hash(
        slug = slug,
        proposals_ref = proposals_ref,
        source_keys = relevant_files,
        content_fp = domains.dd.planner.nodes.chapter_propose.domain.fingerprint(
            {"proposals": proposals_dicts, "distillates": distillates},
        ),
    )
    vkey = keys.versioned_key(slug, manifest)
    lkey = keys.latest_key(slug)
    if await minio.exists(vkey) and await minio.exists(lkey):
        try:
            cached = json.loads(await minio.read_text(vkey))
            wall_ms = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_docs": len(cached.get("assignments") or {}),
                "n_proposals": len(proposals_dicts),
                "cache_hit": True,
                "wall_ms": wall_ms,
                "manifest_hash": manifest,
            }
            await domains.dd.planner.runtime.progress.service.emit_progress(
                thread_id, "chapter_assign", "done",
                cache_hit = True,
                n_docs = stats["n_docs"],
                wall_ms = wall_ms,
            )
            return {
                "chapter_doc_assignments_ref": lkey,
                "assign_stats": stats,
            }
        except Exception:
            pass

    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "chapter_assign", "start",
        n_docs = len(relevant_files),
        n_proposals = len(proposals_dicts),
    )

    # Settle window — see SETTLE_DELAY_S. Only reached past the cache-hit
    # check above, so a fully-cached re-plan never pays this cost.
    if params.SETTLE_DELAY_S > 0:
        await domains.dd.planner.runtime.progress.service.emit_progress(
            thread_id, "chapter_assign", "settling", delay_s = params.SETTLE_DELAY_S,
        )
        # SETTLE_DELAY_S is now the CAP: proceeds as soon as the Rotator pool has
        # recovered (<=25% of arms cooling, min 10s), full wait if it can't tell.
        settled = await domains.settings.chat.service.wait_for_rotator_settle(params.SETTLE_DELAY_S)
        logger.info(f"[chapter_assign] settle done: {settled}")

    # Bulk prefetch bodies only for docs lacking distillate summary (fallback path)
    # SOTA: single read_many chunked (shared S3 client, BoundedSemaphore per chunk)
    # vs per-doc read_text inside semaphore (wasted LLM slot). Truncate to BODY_CHARS.
    need_body_keys = [
        k for k in relevant_files
        if not ((distillates.get(k) or {}).get("summary") or "").strip()
    ]
    body_map: dict[str, str] = {}
    if need_body_keys:
        try:
            bodies = await minio.read_many(need_body_keys)  # type: ignore[attr-defined]
            if bodies is not None and len(bodies) == len(need_body_keys):
                body_map = {k: (b or "")[:params.BODY_CHARS] for k, b in zip(need_body_keys, bodies)}
            else:
                raise RuntimeError("read_many length mismatch")
        except Exception as e:
            logger.warning(f"[chapter_assign] bulk read_many failed ({type(e).__name__}: {e}), fallback per-key")
            async def _read_one(k: str) -> tuple[str, str]:
                try:
                    return k, (await minio.read_text(k))[:params.BODY_CHARS]
                except Exception:
                    return k, ""
            results_one = await asyncio.gather(*[_read_one(k) for k in need_body_keys])
            body_map = dict(results_one)

    doc_path: dict[str, str] = {}
    if params.URL_PATH_IN_PROMPT:
        try:
            manifest_obj = await domains.dd.ingestion.storage.service.read_framework_manifest(minio, slug)
            doc_path = domain.url_paths((manifest_obj or {}).get("entries") or [])
        except Exception as e:
            logger.info(f"[chapter_assign] {slug}: manifest unavailable, no URL paths in the prompt ({type(e).__name__})")

    def _task(k: str, sem_: asyncio.Semaphore):
        return assign_one(
            sem_, slug, k,
            distillates.get(k), proposals_dicts,
            doc_body = body_map.get(k, ""),
            doc_path = doc_path.get(k, ""),
        )

    sem = asyncio.Semaphore(params.CONCURRENCY)
    results = await asyncio.gather(*[_task(k, sem) for k in relevant_files], return_exceptions = False)
    results, n_swept, n_sweep_recovered = await sweep_failed(
        results, _task, len(relevant_files),
        settle = lambda: domains.settings.chat.service.wait_for_rotator_settle(params.SETTLE_DELAY_S),
    )
    if n_swept:
        logger.info(f"[chapter_assign] {slug}: fallback sweep recovered {n_sweep_recovered}/{n_swept} doc(s)")

    assignments: dict[str, list[dict]] = {}
    n_failed = 0
    fallbacks: list[str] = []
    for k, scores, _wall, used_fb in results:
        if scores is None:
            n_failed += 1
            continue
        assignments[k] = scores
        if used_fb:
            fallbacks.append(k)

    # RESCUE PASS: docs in [RESCUE_FLOOR, CONFIDENCE_THRESHOLD) get their best score floored to threshold so they aren't silently dropped at chapter_select.
    rescued = domain.apply_rescue_pass(assignments)

    if rescued:
        logger.warning(
            f"[chapter_assign] {slug}: rescued {len(rescued)} doc(s) "
            f"from the [{params.RESCUE_FLOOR}, {params.CONFIDENCE_THRESHOLD}) confidence "
            f"band — floored best score to {params.CONFIDENCE_THRESHOLD} so they "
            f"reach a chapter instead of being silently dropped at "
            f"chapter_select. Sample: "
            f"{[(r['key'], r['original_conf']) for r in rescued[:10]]}"
        )
        infra.langfuse.service.flag_for_review(
            f"chapter_assign rescued {len(rescued)} doc(s) in confidence "
            f"band [{params.RESCUE_FLOOR}, {params.CONFIDENCE_THRESHOLD})",
            severity = "low",
        )

    # Rebuild coverage_count AFTER rescue so the post-rescue picture is
    # what flows into the stats payload.
    coverage_count = domain.compute_coverage_count(assignments, len(proposals_dicts))

    if fallbacks:
        logger.warning(
            f"[chapter_assign] {slug}: {len(fallbacks)} doc(s) used a "
            f"lexical fallback assignment (assign LLM failed but doc "
            f"kept): {fallbacks[:20]}"
        )

    payload = {
        "prompt_version":     versions.PROMPT_VERSION,
        "framework_slug":     slug,
        "manifest_hash":      manifest,
        "assignments":        assignments,
        "n_docs":             len(relevant_files),
        "n_assigned":         len(assignments),
        "n_failed":           n_failed,
        "n_fallback":         len(fallbacks),
        "fallbacks":          fallbacks[:20],
        "n_swept":            n_swept,
        "n_sweep_recovered":  n_sweep_recovered,
        "n_rescued":          len(rescued),
        "rescued":            rescued[:20],
        "n_proposals":        len(proposals_dicts),
        "coverage_count":     coverage_count,
        "confidence_thresh":  params.CONFIDENCE_THRESHOLD,
        "rescue_floor":       params.RESCUE_FLOOR,
    }
    blob = json.dumps(payload, indent = 2, ensure_ascii = False)
    await minio.write(vkey, blob, content_type = "application/json")
    await minio.write(lkey, blob, content_type = "application/json")

    wall_ms = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_docs": len(relevant_files),
        "n_assigned": len(assignments),
        "n_failed": n_failed,
        "n_fallback": len(fallbacks),
        "n_swept": n_swept,
        "n_sweep_recovered": n_sweep_recovered,
        "n_rescued": len(rescued),
        "n_proposals": len(proposals_dicts),
        "coverage_count": coverage_count,
        "cache_hit": False,
        "wall_ms": wall_ms,
        "manifest_hash": manifest,
    }
    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "chapter_assign", "done",
        cache_hit = False,
        n_assigned = len(assignments),
        n_failed = n_failed,
        n_fallback = len(fallbacks),
        n_rescued = len(rescued),
        wall_ms = wall_ms,
    )
    return {
        "chapter_doc_assignments_ref": lkey,
        "assign_stats": stats,
    }
