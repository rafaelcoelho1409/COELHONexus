from __future__ import annotations
import domains
from . import domain, keys, params, schemas, versions

import asyncio
import json
import logging
import time
from typing import Optional


logger = logging.getLogger(__name__)

async def _detect_semantic_h2_duplicates(
    outline: schemas.ChapterOutline,
    *,
    threshold: float = params.SEMANTIC_H2_DEDUP_THRESHOLD,
) -> list[str]:
    """Return issues for scope-duplicate H2 pairs (embedding cosine OR lexical overlap). Fail-soft: lexical pass still runs when embedder is unavailable."""
    sections = outline.sections
    if len(sections) <= 1:
        return []
    n = len(sections)
    words = [domain.scope_words(f"{s.heading} {s.description}") for s in sections]

    # Embedding cosine (semantic signal) — best-effort.
    # 2026-09-18: was a local in-process FastEmbed helper (a 2026-08-25-era
    # workaround for a since-retired NIM embedding model going EOL), only
    # reaching an external provider as a last-resort fallback. Switched to
    # `domains.settings.embeddings.service.embed_texts_async`, the genuine
    # external-provider path YCS's own embedding pipeline already migrated
    # to on 2026-09-13 for the same reason. Safe here specifically because this whole block is
    # explicitly fail-soft (see docstring) — any embedding failure,
    # slow or fast, already falls back to the lexical-only comparison
    # below, unchanged.
    sim = None
    try:
        embeddings, _model = await domains.settings.embeddings.service.embed_texts_async(
            [f"{s.heading}\n{s.description}" for s in sections],
        )
        import numpy as np
        embs = np.array(embeddings, dtype=np.float32)
        norms = np.linalg.norm(embs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        normed = embs / norms
        sim = normed @ normed.T
    except Exception as e:
        logger.warning(
            f"[outline_sdp] semantic H2 dedup embed/cosine failed: "
            f"{type(e).__name__}: {e} — falling back to lexical scope check"
        )
        sim = None

    flagged: list[tuple[str, str, float, str]] = []
    for i in range(n):
        for j in range(i + 1, n):
            cos = float(sim[i, j]) if sim is not None else 0.0
            wi, wj = words[i], words[j]
            jac = (len(wi & wj) / len(wi | wj)) if (wi or wj) else 0.0
            if cos >= threshold or jac >= params.SCOPE_LEXICAL_JACCARD:
                flagged.append((
                    sections[i].heading, sections[j].heading,
                    max(cos, jac),
                    "cosine" if cos >= threshold else "lexical",
                ))
    if not flagged:
        return []
    pair_strs = [
        f"{a!r} ↔ {b!r} ({s:.0%} {w})" for a, b, s, w in flagged[:3]
    ]
    suffix = (
        f", +{len(flagged) - 3} more pairs" if len(flagged) > 3 else ""
    )
    return [
        f"Scope-duplicate H2 section pairs detected ({len(flagged)} "
        f"pair(s); embedding cosine ≥ {threshold:.0%} OR content-word "
        f"overlap ≥ {params.SCOPE_LEXICAL_JACCARD:.0%}): "
        f"{', '.join(pair_strs)}{suffix}. These sections cover the SAME "
        f"scope (same APIs / examples) with different wording — MERGE each "
        f"pair into ONE section under a unified heading, OR re-scope one to "
        f"a genuinely distinct capability. Overlapping sections make the "
        f"writer recycle code; the renderer then strips the duplicates, "
        f"leaving hollow 'see other section' sections."
    ]


async def _generate_samples(
    prompt: str, n: int, thread_id: str,
    *,
    n_sources: int | None = None,
) -> list[tuple[dict, dict]]:
    """Fire N drafts with Optimal-Stopping (arXiv 2510.01394): sample 1 checked first; if clean + valid + ≥ min sections, skip remaining N-1. Else fan out concurrently, then USC vote. Disabled via KD_OUTLINE_OPTIMAL_STOPPING=false."""
    if params.OPTIMAL_STOPPING_ENABLED and n >= 2:
        r0 = await _draft_one_outline(
            prompt, sample_idx=0, n_total=n, thread_id=thread_id,
        )
        results: list = [r0]
        parsed0, _meta0 = r0
        if parsed0 is not None:
            outline0, _err = domain.try_parse_outline(parsed0)
            if outline0 is not None:
                dag0 = domain.derive_dag(outline0.sections)
                _, issues0 = domain.validate_outline_structure(
                    outline0, dag0, n_sources=n_sources,
                )
                if (
                    not issues0
                    and len(outline0.sections)
                        >= domain.outline_optimal_stopping_min(n_sources)
                ):
                    logger.info(
                        f"[outline_sdp] Optimal-Stopping fired — sample 0 "
                        f"clean ({len(outline0.sections)} sections, 0 issues); "
                        f"skipping remaining {n - 1} samples"
                    )
                    successful: list[tuple[dict, dict]] = []
                    if parsed0 is not None:
                        successful.append(r0)
                    return successful
        remaining = await asyncio.gather(*[
            _draft_one_outline(
                prompt, sample_idx=i, n_total=n, thread_id=thread_id,
            )
            for i in range(1, n)
        ])
        results.extend(remaining)
    else:
        results = await asyncio.gather(*[
            _draft_one_outline(
                prompt, sample_idx=i, n_total=n, thread_id=thread_id,
            )
            for i in range(n)
        ])
    successful: list[tuple[dict, dict]] = []
    for parsed, meta in results:
        if parsed is not None:
            successful.append((parsed, meta))
        else:
            logger.info(
                f"[outline_sdp] draft failed: {meta.get('error', 'unknown')}"
            )
    return successful

async def _usc_pick(
    candidates: list[tuple[schemas.ChapterOutline, schemas.OutlineDAG, list[str]]],
    chapter_id: str,
    chapter_title: str,
    adaptive_cap: int,
) -> int:
    """Run USC picker over candidates. adaptive_cap = per-chapter section ceiling; picker rewards candidates at or just under it. Falls back to index 0 on failure."""
    if len(candidates) <= 1:
        return 0
    summaries = [
        domain.summarize_candidate(o, d, issues)
        for (o, d, issues) in candidates
    ]
    prompt = domain.build_usc_vote_prompt(
        candidates_summary=summaries,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        adaptive_cap=adaptive_cap,
    )
    try:
        response, _ = await domains.settings.chat.service.chat_text_async(
            prompt,
            max_tokens=params.MAX_TOKENS_VOTE,
            temperature=params.TEMPERATURE_VOTE,
            response_format=schemas.USC_VOTE_RESPONSE_FORMAT,
            timeout_s=params.TIMEOUT_S_VOTE,
        )
        parsed = domain.parse_json_response(response)
        if parsed and "chosen_index" in parsed:
            idx = int(parsed["chosen_index"])
            if 0 <= idx < len(candidates):
                return idx
    except Exception as e:
        logger.warning(
            f"[outline_sdp] USC picker failed: "
            f"{type(e).__name__}: {e} — falling back to first candidate"
        )
    return 0


async def _draft_one_outline(
    prompt: str,
    *,
    sample_idx: int,
    n_total: int,
    thread_id: str,
) -> tuple[Optional[dict], dict]:
    """One LLM call for outline draft. Emits `sample_done` SSE per sample so UI shows per-sample progress during asyncio.gather (otherwise silent for ~30s)."""
    t0 = time.monotonic()
    try:
        response, meta = await domains.settings.chat.service.chat_text_async(
            prompt,
            max_tokens=params.MAX_TOKENS_DRAFT,
            temperature=params.TEMPERATURE_DRAFT,
            response_format=schemas.OUTLINE_RESPONSE_FORMAT,
            timeout_s=params.TIMEOUT_S_DRAFT,
        )
    except Exception as e:
        error_tag = (
            "context_overflow" if domain.is_context_overflow_error(e)
            else f"{type(e).__name__}: {str(e)[:200]}"
        )
        await domains.dd.synth.runtime.progress.service.emit_progress(
            thread_id, "outline_sdp", "sample_done",
            sample_idx=sample_idx, n_total=n_total,
            ok=False, error=f"{type(e).__name__}: {str(e)[:120]}",
            wall_ms=int((time.monotonic() - t0) * 1000),
        )
        return None, {"error": error_tag}
    parsed = domain.parse_json_response(response)
    if not parsed:
        await domains.dd.synth.runtime.progress.service.emit_progress(
            thread_id, "outline_sdp", "sample_done",
            sample_idx=sample_idx, n_total=n_total,
            ok=False, error="parse_failed",
            wall_ms=int((time.monotonic() - t0) * 1000),
            deployment=meta.get("deployment"),
        )
        return None, {
            **meta,
            "error": "parse_failed",
            "raw":   (response or "")[:200],
        }
    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "outline_sdp", "sample_done",
        sample_idx=sample_idx, n_total=n_total,
        ok=True,
        wall_ms=int((time.monotonic() - t0) * 1000),
        deployment=meta.get("deployment"),
        n_sections=len(parsed.get("sections") or []),
    )
    return parsed, meta

async def outline_sdp_run(state: domains.dd.synth.state.SynthState) -> dict:
    """Run the Structure-Driven Planner for one chapter."""
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    thread_id = state.get("thread_id") or ""

    if not slug or not chapter_id:
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped": "no_slug_or_chapter_id",
                "wall_ms": 0,
            },
            "status": "failed",
            "error":  "framework_slug or chapter_id missing from SynthState",
        }

    t0 = time.monotonic()
    minio = domains.dd.ingestion.storage.service.get_storage()

    plan_key = domains.dd.synth.nodes.render.keys.planner_latest_key(slug)
    if not await minio.exists(plan_key):
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped": "plan_not_found",
                "plan_key": plan_key,
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"planner plan {plan_key!r} not in MinIO; run planner first",
        }

    plan_text = await minio.read_text(plan_key)
    try:
        plan = json.loads(plan_text)
    except Exception as e:
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped": "plan_unreadable",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"plan-latest.json unreadable: {type(e).__name__}: {e}",
        }

    chapter = domain.find_chapter(plan, chapter_id)
    if chapter is None:
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped":     "chapter_not_in_plan",
                "wall_ms":     int((time.monotonic() - t0) * 1000),
                "known_ids":   [c.get("id") for c in (plan.get("chapters") or [])],
            },
            "status": "failed",
            "error":  f"chapter {chapter_id!r} not in plan-latest.json",
        }

    chapter_title       = chapter.get("title") or chapter_id
    chapter_description = chapter.get("description") or ""
    sources             = sorted(chapter.get("sources") or [])
    if not sources:
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped": "no_sources",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"chapter {chapter_id!r} has zero sources in plan",
        }

    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "outline_sdp", "start",
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        n_sources = len(sources),
    )

    # Each source is already corpus_normalized + vault_sentinelized by
    # ingestion (architecture cleanup). We just concat.
    bodies = await minio.read_many(sources)
    bodies = [b for b in bodies if b]
    if not bodies:
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped": "source_bodies_empty",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  "all source bodies came back empty",
        }
    sources_concat_md, truncated = domain.concat_sources(bodies)
    n_vault_hashes = domain.count_vault_sentinels(sources_concat_md)

    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "outline_sdp", "sources_loaded",
        n_sources = len(sources),
        n_bodies = len(bodies),
        bytes = len(sources_concat_md),
        truncated = truncated,
        n_vault_hashes = n_vault_hashes,
    )

    manifest_hash = domain.compute_manifest_hash(
        sources = sources,
        sources_bytes = len(sources_concat_md),
        chapter_title = chapter_title,
        chapter_description = chapter_description,
    )
    versioned_key = keys.versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = keys.latest_blob_key(slug, chapter_id)

    if await minio.exists(versioned_key) and await minio.exists(latest_key):
        try:
            cached_text = await minio.read_text(versioned_key)
            cached = json.loads(cached_text)
            outline_dict = (cached or {}).get("outline") or {}
            dag_dict     = (cached or {}).get("dag") or {}
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_sections":   len(outline_dict.get("sections") or []),
                "max_stage":    int(dag_dict.get("max_stage", 0)),
                "n_stages":     len(dag_dict.get("stages") or {}),
                "n_removed_edges": len(dag_dict.get("removed_edges") or []),
                "wall_ms":      elapsed,
                "store_path":   latest_key,
                "versioned_path": versioned_key,
                "manifest_hash":  manifest_hash,
                "cache_hit":    True,
                "prompt_version": cached.get("prompt_version"),
            }
            await domains.dd.synth.runtime.progress.service.emit_progress(
                thread_id, "outline_sdp", "done",
                n_sections = stats["n_sections"],
                max_stage = stats["max_stage"],
                wall_ms = elapsed, cache_hit = True,
            )
            logger.info(
                f"[outline_sdp] {slug}/{chapter_id}: CACHE HIT — "
                f"{stats['n_sections']} sections, max_stage = "
                f"{stats['max_stage']}, {elapsed} ms"
            )
            return {"outline_path": latest_key, "outline_stats": stats}
        except Exception as e:
            logger.warning(
                f"[outline_sdp] {slug}/{chapter_id}: cached blob "
                f"{versioned_key!r} unreadable ({type(e).__name__}: {e}); "
                f"recomputing"
            )

    # Adaptive cap (not fixed 8): old fixed-8 pushed LLM to over-section small chapters (then hard-trimmed or deadlocked). Correct count up front → winner rarely needs trimming.
    adaptive_target = params.max_h2_for_n_sources(len(sources))
    prompt = domain.build_outline_prompt(
        framework = slug,
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        chapter_description = chapter_description,
        n_vault_hashes = n_vault_hashes,
        sources_concat_md = sources_concat_md,
        target_sections_hint = adaptive_target,
    )
    raw_samples = await _generate_samples(
        prompt, params.N_SAMPLES, thread_id, n_sources = len(sources),
    )

    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "outline_sdp", "samples_drafted",
        n_samples = len(raw_samples), n_requested = params.N_SAMPLES,
    )

    candidates: list[tuple[schemas.ChapterOutline, schemas.OutlineDAG, list[str]]] = []
    pydantic_failures = 0
    for parsed_dict, meta in raw_samples:
        outline, err = domain.try_parse_outline(parsed_dict)
        if outline is None:
            pydantic_failures += 1
            logger.info(
                f"[outline_sdp] {slug}/{chapter_id}: pydantic-reject — {err}"
            )
            continue
        dag = domain.derive_dag(outline.sections)
        _, issues = domain.validate_outline_structure(
            outline, dag, n_sources = len(sources),
        )
        candidates.append((outline, dag, issues))

    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "outline_sdp", "samples_validated",
        n_candidates = len(candidates), n_pydantic_fail = pydantic_failures,
    )

    if not candidates:
        overflow_count = sum(
            1 for _, meta in raw_samples
            if (meta or {}).get("error") == "context_overflow"
        )
        if overflow_count >= max(1, len(raw_samples) // 2):
            # Predominantly context-overflow, not generic hiccups — the
            # Rotator's arm pool is heterogeneous and un-filtered by
            # context length (see _is_context_overflow_error), so this
            # sample batch likely landed on a small-context arm. Retry
            # once at half the source budget before giving up to the
            # heuristic fallback.
            retry_budget = max(domain.MAX_SOURCE_CHARS // 2, 20_000)
            logger.warning(
                f"[outline_sdp] {slug}/{chapter_id}: {overflow_count}/"
                f"{len(raw_samples)} samples hit context overflow — "
                f"retrying once at {retry_budget} chars (was "
                f"{len(sources_concat_md)})"
            )
            sources_concat_md, truncated = domain.concat_sources(
                bodies, max_chars = retry_budget,
            )
            n_vault_hashes = domain.count_vault_sentinels(sources_concat_md)
            retry_prompt = domain.build_outline_prompt(
                framework = slug,
                chapter_id = chapter_id,
                chapter_title = chapter_title,
                chapter_description = chapter_description,
                n_vault_hashes = n_vault_hashes,
                sources_concat_md = sources_concat_md,
                target_sections_hint = adaptive_target,
            )
            raw_samples = await _generate_samples(
                retry_prompt, params.N_SAMPLES, thread_id, n_sources = len(sources),
            )
            for parsed_dict, meta in raw_samples:
                outline, err = domain.try_parse_outline(parsed_dict)
                if outline is None:
                    continue
                dag = domain.derive_dag(outline.sections)
                _, issues = domain.validate_outline_structure(
                    outline, dag, n_sources = len(sources),
                )
                candidates.append((outline, dag, issues))

    if not candidates:
        logger.warning(
            f"[outline_sdp] {slug}/{chapter_id}: ALL {params.N_SAMPLES} samples "
            f"failed to parse; emitting heuristic fallback outline"
        )
        outline = domain.heuristic_fallback_outline(sources_concat_md)
        dag = domain.derive_dag(outline.sections)
        candidates = [(outline, dag, ["heuristic_fallback"])]

    chosen_idx = await _usc_pick(
        candidates, chapter_id, chapter_title, adaptive_cap = adaptive_target,
    )
    outline, dag, issues = candidates[chosen_idx]

    # Embed heading+description pairs above similarity threshold → repair-loop feedback. Fail-soft.
    semantic_dupe_issues = await _detect_semantic_h2_duplicates(outline)
    if semantic_dupe_issues:
        issues = list(issues) + semantic_dupe_issues
        logger.info(
            f"[outline_sdp] {slug}/{chapter_id}: semantic H2 dedup found "
            f"{len(semantic_dupe_issues)} feedback message(s); will drive "
            f"repair loop"
        )

    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "outline_sdp", "usc_voted",
        chosen_index = chosen_idx, n_initial_violations = len(issues),
    )

    n_repairs = 0
    for attempt in range(params.MAX_REPAIR_RETRIES):
        if not issues:
            break
        n_repairs += 1
        await domains.dd.synth.runtime.progress.service.emit_progress(
            thread_id, "outline_sdp", "repair_attempt",
            attempt = attempt + 1,
            n_violations = len(issues),
        )
        repair_prompt = domain.build_repair_prompt(
            framework = slug,
            chapter_id = chapter_id,
            chapter_title = chapter_title,
            chapter_description = chapter_description,
            current_outline_json = json.dumps(outline.model_dump(), indent = 2),
            issues = issues,
            sources_concat_md = sources_concat_md,
        )
        try:
            repair_response, _ = await domains.settings.chat.service.chat_text_async(
                repair_prompt,
                max_tokens = params.MAX_TOKENS_REPAIR,
                temperature = params.TEMPERATURE_REPAIR,
                response_format = schemas.OUTLINE_RESPONSE_FORMAT,
                timeout_s = params.TIMEOUT_S_REPAIR,
            )
            parsed = domain.parse_json_response(repair_response)
            if not parsed:
                logger.warning(
                    f"[outline_sdp] {slug}/{chapter_id}: repair attempt "
                    f"{attempt + 1} produced unparseable JSON; keeping prior"
                )
                continue
            new_outline, err = domain.try_parse_outline(parsed)
            if new_outline is None:
                logger.warning(
                    f"[outline_sdp] {slug}/{chapter_id}: repair attempt "
                    f"{attempt + 1} pydantic-rejected: {err}"
                )
                continue
            new_dag = domain.derive_dag(new_outline.sections)
            _, new_issues = domain.validate_outline_structure(
                new_outline, new_dag, n_sources = len(sources),
            )
            # Re-check semantic H2 dedup on repaired outline so repair loop credits the LLM's response to feedback.
            new_semantic = await _detect_semantic_h2_duplicates(new_outline)
            if new_semantic:
                new_issues = list(new_issues) + new_semantic
            # Only accept if it ACTUALLY improves things.
            if len(new_issues) <= len(issues):
                outline = new_outline
                dag = new_dag
                issues = new_issues
        except Exception as e:
            logger.warning(
                f"[outline_sdp] {slug}/{chapter_id}: repair attempt "
                f"{attempt + 1} failed: {type(e).__name__}: {e}"
            )
            continue

    # HARD-TRIM: LLM ignores soft cap signal (BU ch-02 shipped 30 H2 at cap 12; CC ch-01 shipped 20 at cap 14). Trim to adaptive_cap directly — old max(SECTIONS_MIN, cap) floor caused NO-OP for 4-section outlines below cap 4.
    adaptive_cap = params.max_h2_for_n_sources(len(sources))
    if len(outline.sections) > adaptive_cap:
        n_before = len(outline.sections)
        # Topological order: lower stage_index first.
        sections_by_stage: list[tuple[int, schemas.OutlineSection]] = []
        sid_to_section = {s.section_id: s for s in outline.sections}
        for stage_idx in sorted(dag.stages.keys()):
            for sid in dag.stages[stage_idx]:
                if sid in sid_to_section:
                    sections_by_stage.append((stage_idx, sid_to_section[sid]))
        # Sections not in any stage (orphans) appended last.
        seen = {s.section_id for _, s in sections_by_stage}
        for s in outline.sections:
            if s.section_id not in seen:
                sections_by_stage.append((dag.max_stage + 1, s))
        kept = [s for _, s in sections_by_stage[:adaptive_cap]]
        kept_ids = {s.section_id for s in kept}
        # Clean prereqs that point to dropped sections.
        for s in kept:
            s.prerequisites = [p for p in s.prerequisites if p in kept_ids]
        outline = outline.model_copy(update = {"sections": kept})
        dag = domain.derive_dag(outline.sections)
        logger.warning(
            f"[outline_sdp] {slug}/{chapter_id}: HARD-TRIM outline "
            f"{n_before} → {len(outline.sections)} sections (adaptive_cap = "
            f"{adaptive_cap}, n_sources = {len(sources)}). LLM ignored the "
            f"soft cap signal after {n_repairs} repairs; programmatic "
            f"trim restores the bound."
        )
        domains.dd.synth.runtime.observability.metrics.record_bucket_split_overflow(
            framework = slug,
            sections_dropped = max(n_before - len(outline.sections), 0),
        )
        # Re-validate post-trim so downstream sees actual remaining violations (not pre-trim ones including the now-resolved cap-exceeded).
        _, issues = domain.validate_outline_structure(
            outline, dag, n_sources = len(sources),
        )
        # Re-check semantic dedup post-trim (trimming may have removed near-duplicate H2s).
        post_trim_semantic = await _detect_semantic_h2_duplicates(outline)
        if post_trim_semantic:
            issues = list(issues) + post_trim_semantic
        await domains.dd.synth.runtime.progress.service.emit_progress(
            thread_id, "outline_sdp", "hard_trimmed",
            n_before = n_before, n_after = len(outline.sections),
            adaptive_cap = adaptive_cap, n_sources = len(sources),
        )

    final_violations = issues

    payload = domain.serialize_outline_with_dag(outline, dag)
    payload["framework_slug"]   = slug
    payload["chapter_id"]       = chapter_id
    payload["chapter_title"]    = chapter_title
    payload["manifest_hash"]    = manifest_hash
    payload["source_keys"]      = sources
    payload["n_vault_hashes"]   = n_vault_hashes
    payload["truncated"]        = truncated
    payload["n_repairs"]        = n_repairs
    payload["final_violations"] = final_violations

    blob_bytes = json.dumps(payload, indent = 2, ensure_ascii = False)
    await minio.write(
        versioned_key, blob_bytes, content_type = "application/json",
    )
    await minio.write(
        latest_key, blob_bytes, content_type = "application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_sections":     len(outline.sections),
        "max_stage":      dag.max_stage,
        "n_stages":       len(dag.stages),
        "n_removed_edges": len(dag.removed_edges),
        "n_repairs":      n_repairs,
        "n_violations":   len(final_violations),
        "violations":     final_violations,
        "n_samples":      len(candidates),
        "n_vault_hashes": n_vault_hashes,
        "truncated":      truncated,
        "wall_ms":        elapsed,
        "store_path":     latest_key,
        "versioned_path": versioned_key,
        "manifest_hash":  manifest_hash,
        "cache_hit":      False,
        "prompt_version": versions.OUTLINE_PROMPT_VERSION,
    }
    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "outline_sdp", "done",
        n_sections = stats["n_sections"],
        max_stage = stats["max_stage"],
        n_repairs = n_repairs,
        n_violations = stats["n_violations"],
        wall_ms = elapsed,
    )
    logger.info(
        f"[outline_sdp] {slug}/{chapter_id}: {stats['n_sections']} "
        f"sections, max_stage = {stats['max_stage']}, "
        f"n_stages = {stats['n_stages']}, n_repairs = {n_repairs}, "
        f"violations = {len(final_violations)}, {elapsed} ms"
    )
    return {"outline_path": latest_key, "outline_stats": stats}


# Convenience loader for downstream nodes
