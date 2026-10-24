"""order_chapters I/O shell — one LLM sample call (bandit-routed) + the
order_chapters_run orchestration.

SOTA Sept 2026 on the rotator pooled:
- Pooled AsyncOpenAI http2 200/100 handles N_SAMPLES parallel 800 tok
  via Borda (old bandit 5-way serialized). N=3 USC sweet spot.
- JSON response_format for strict parsing (vs regex fallback).
- Static prefix (pedagogical rubric) before dynamic chapter block → KV-cache.
"""
from __future__ import annotations
import domains
from . import domain, keys, params, prompts, schemas, versions

import asyncio
import json
import logging
import random
import time
from hashlib import sha256


logger = logging.getLogger(__name__)


async def sample_one_ordering(
    sem: asyncio.Semaphore,
    chapters: list[dict],
    composition: list[dict | None] | None,
    framework: str,
    sample_idx: int,
) -> tuple[list[int] | None, dict]:
    """One LLM call + one reask-on-failure. The model returns chapter TITLES in reading order
    (mapped back to indices by domain.titles_to_order); each sample sees the chapters in its
    own shuffled arrangement. Returns (parsed_order_or_None, meta)."""
    labels = domain.chapter_labels(chapters)
    prompt = prompts.build_order_prompt(chapters, composition, framework, sample_idx)
    order, bad = None, None
    meta: dict = {}
    async with sem:
        try:
            result, meta = await domains.settings.chat.service.chat_structured_async(
                prompt,
                schemas.ChapterTitleOrder,
                max_tokens = params.MAX_TOKENS,
                temperature = params.TEMPERATURE,
                timeout_s = params.TIMEOUT_S,
            )
            order = domain.titles_to_order(result.order, labels)
            bad = None if order is not None else result.order
        except ValueError:
            # Didn't even parse into the schema — same reask-eligible
            # bucket as a schema-valid-but-unusable response.
            order, bad, meta = None, None, {}
        except Exception as e:
            return None, {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    if order is not None:
        return order, meta

    repair_prompt = (
        prompt
        + f"\n\nPRIOR OUTPUT was invalid/unparseable (raw={str(bad)[:200]!r}). Return ALL {len(labels)} "
        f"chapter titles exactly as listed above, each once, as valid JSON per the schema."
    )
    async with sem:
        try:
            result2, meta2 = await domains.settings.chat.service.chat_structured_async(
                repair_prompt,
                schemas.ChapterTitleOrder,
                max_tokens = params.MAX_TOKENS,
                temperature = 0.0,
                timeout_s = params.TIMEOUT_S,
            )
        except Exception as e:
            return None, {"error": f"reask {type(e).__name__}: {str(e)[:120]}"}
    order2 = domain.titles_to_order(result2.order, labels)
    if order2 is not None:
        return order2, meta2
    return None, {
        **meta2,
        "error": "parse_failed_after_reask",
        "raw": str(result2.order)[:120],
    }


async def order_chapters_run(state: domains.dd.planner.state.PlannerState) -> dict:
    """Sample N orderings (bandit) → Borda-aggregate → foundational-prefix → persist."""
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    reduce_ref = state.get("chapter_plan_ref") or ""
    if not slug or not reduce_ref:
        return {
            "chapter_order_ref": "",
            "order_chapters_stats": {
                "skipped": "no_input", "n_chapters": 0, "wall_ms": 0,
            },
        }

    t0 = time.monotonic()

    minio = domains.dd.ingestion.storage.service.get_storage()
    # Identity follows the chapter plan's CONTENT: `reduce_ref` is the constant
    # "latest" path, so hashing it alone served the previous run's order for a
    # different chapter set (asyncio: the same order file name for a 12- and a
    # 10-chapter plan) — silently wrong when the chapter count happened to match.
    try:
        _content = await minio.read_text(reduce_ref)
    except Exception:
        _content = ""
    mh = sha256(
        f"reduce={reduce_ref}#{sha256(_content.encode('utf-8')).hexdigest()[:16]}"
        f"|n={params.N_SAMPLES}|v={versions.PROMPT_VERSION}".encode("utf-8"),
    ).hexdigest()[:16]
    cache_key = keys.blob_key(slug, mh)

    if await minio.exists(cache_key):
        try:
            cached = json.loads(await minio.read_text(cache_key))
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_chapters":     cached.get("n_chapters", 0),
                "n_samples":      len(cached.get("samples") or []),
                "foundational":   cached.get("foundational_idx") or [],
                "order":          cached.get("order") or [],
                "wall_ms":        elapsed,
                "store_path":     cache_key,
                "cache_hit":      True,
                "prompt_version": cached.get("prompt_version"),
            }
            await domains.dd.planner.runtime.progress.service.emit_progress(
                thread_id, "order_chapters", "done",
                n_chapters = stats["n_chapters"],
                wall_ms = elapsed,
                cache_hit = True,
            )
            logger.info(
                f"[order_chapters] {slug}: CACHE HIT — "
                f"order={stats['order']}, foundational="
                f"{stats['foundational']}, {elapsed} ms"
            )
            return {
                "chapter_order_ref": cache_key,
                "order_chapters_stats": stats,
            }
        except Exception as e:
            logger.warning(
                f"[order_chapters] {slug}: cached blob {cache_key!r} "
                f"unreadable ({type(e).__name__}: {e}); recomputing"
            )

    await domains.dd.planner.runtime.progress.service.emit_progress(thread_id, "order_chapters", "start")

    try:
        reduce_text = await minio.read_text(reduce_ref)
        outline = domain.load_outline(reduce_text)
    except Exception as e:
        elapsed = int((time.monotonic() - t0) * 1000)
        logger.warning(
            f"[order_chapters] {slug}: reduce outline unreadable "
            f"({type(e).__name__}: {e}); identity order"
        )
        return {
            "chapter_order_ref": "",
            "order_chapters_stats": {
                "skipped": "outline_unreadable",
                "error": f"{type(e).__name__}: {str(e)[:120]}",
                "wall_ms": elapsed,
            },
        }
    chapters = (outline or {}).get("chapters") or []
    n_chapters = len(chapters)

    if n_chapters <= 1:
        # 0 or 1 chapter — no meaningful ordering. Persist trivial blob
        # so downstream plan_write sees the file and skips its own
        # reorder.
        elapsed = int((time.monotonic() - t0) * 1000)
        identity = list(range(n_chapters))
        payload = {
            "order":            identity,
            "samples":          [],
            "foundational_idx": [],
            "n_chapters":       n_chapters,
            "prompt_version":   versions.PROMPT_VERSION,
            "deployment_usage": [],
            "skipped":          "trivial_n_chapters",
        }
        await minio.write(
            cache_key, json.dumps(payload),
            content_type = "application/json",
        )
        await domains.dd.planner.runtime.progress.service.emit_progress(
            thread_id, "order_chapters", "done",
            n_chapters = n_chapters, wall_ms = elapsed,
            skipped = "trivial_n_chapters",
        )
        return {
            "chapter_order_ref": cache_key,
            "order_chapters_stats": {
                "n_chapters": n_chapters, "wall_ms": elapsed,
                "store_path": cache_key, "cache_hit": False,
                "order": identity, "foundational": [],
                "skipped": "trivial_n_chapters",
            },
        }

    # Source order: where each chapter's pages sit in the docs' own navigation.
    source_ranking: list[int] | None = None
    key_dir: dict[str, tuple] = {}
    try:
        _m = await domains.dd.ingestion.storage.service.read_framework_manifest(minio, slug)
        _entries = (_m or {}).get("entries") or []
        key_dir = domains.dd.planner.nodes.chapter_select.domain.page_dirs(_entries)
        if domain.source_order_informative(_entries):
            _idx = {e["key"]: int(e.get("idx") or 0) for e in _entries if e.get("key")}
            source_ranking = domain.source_order_ranking(chapters, _idx)
        else:
            logger.info(f"[order_chapters] {slug}: source order not informative (single URL or alphabetical) — LLM votes only")
    except Exception as e:
        logger.info(f"[order_chapters] {slug}: source order unavailable ({type(e).__name__})")

    # Settle window — see SETTLE_DELAY_S. Only reached past the cache-hit
    # and trivial-n_chapters checks above.
    if params.SETTLE_DELAY_S > 0:
        await domains.dd.planner.runtime.progress.service.emit_progress(
            thread_id, "order_chapters", "settling", delay_s = params.SETTLE_DELAY_S,
        )
        # SETTLE_DELAY_S is now the CAP: proceeds as soon as the Rotator pool has
        # recovered (<=25% of arms cooling, min 10s), full wait if it can't tell.
        settled = await domains.settings.chat.service.wait_for_rotator_settle(params.SETTLE_DELAY_S)
        logger.info(f"[order_chapters] settle done: {settled}")

    composition = None
    try:
        _dist = await domains.dd.planner.nodes.doc_distill.service.load_distillates(minio, slug)
        composition = domain.chapter_composition(chapters, _dist or {})
    except Exception as e:
        logger.info(f"[order_chapters] {slug}: chapter composition unavailable ({type(e).__name__})")
    sem = asyncio.Semaphore(params.SAMPLE_CONCURRENCY)
    sample_results = await asyncio.gather(*[
        sample_one_ordering(sem, chapters, composition, slug, i)
        for i in range(params.N_SAMPLES)
    ])
    valid_orderings = [r[0] for r in sample_results if r[0] is not None]
    sample_metas = [r[1] for r in sample_results]

    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "order_chapters", "samples_done",
        n_samples = params.N_SAMPLES, n_valid = len(valid_orderings),
        n_failed = params.N_SAMPLES - len(valid_orderings),
    )

    if len(valid_orderings) < params.MIN_VALID_SAMPLES:
        # Too few samples to aggregate (none: retry the whole batch; langfuse 2026-10-14, NIM slow for an hour: 1 of 5
        # valid, so the source order outvoted it 2:1): ask once more for the missing ones after a jittered backoff.
        # Mirrors chapter_propose's outer retry; sample_one_ordering's own inner reask already fired per-sample and
        # wasn't enough during a sustained provider-wide bad patch (2026-09-04 Claude Code docs run: all 3 samples hit
        # parse_failed_after_reask). Jitter (not a flat delay) avoids all the calls re-hammering the same
        # still-recovering deployment in lockstep.
        logger.warning(
            f"[order_chapters] {slug}: {len(valid_orderings)}/{params.N_SAMPLES} samples valid "
            f"({[(m or {}).get('error', 'unknown') for m in sample_metas if (m or {}).get('error')]}) — "
            f"retrying the missing ones once after backoff"
        )
        await asyncio.sleep(2.0 + random.random() * 2.0)
        retry_results = await asyncio.gather(*[
            sample_one_ordering(sem, chapters, composition, slug, i + params.N_SAMPLES)
            for i in range(params.N_SAMPLES - len(valid_orderings))
        ])
        n_before = len(valid_orderings)
        valid_orderings = valid_orderings + [r[0] for r in retry_results if r[0] is not None]
        sample_metas = [m for m in sample_metas if not (m or {}).get("error")] + [r[1] for r in retry_results]
        if len(valid_orderings) > n_before:
            logger.info(
                f"[order_chapters] {slug}: retry recovered "
                f"{len(valid_orderings)}/{params.N_SAMPLES} samples"
            )

    if not valid_orderings:
        # Retry also failed. Fall back to identity order — better than
        # refusing to ship. Persist each sample's actual error (previously
        # computed in sample_metas, then silently discarded) so a repeat
        # of this failure is diagnosable from the stored blob/state instead
        # of needing to catch a warning log before it rolls off.
        elapsed = int((time.monotonic() - t0) * 1000)
        identity = list(source_ranking) if source_ranking else list(range(n_chapters))
        identity, _ = domain.pin_foundations_first(identity, chapters)
        identity, _ = domain.pin_trailing_last(identity, chapters)
        identity, _ = domain.pin_appendix_last(identity, chapters)
        sample_errors = [
            (m or {}).get("error", "unknown") for m in sample_metas
        ]
        payload = {
            "order":            identity,
            "samples":          [],
            "foundational_idx": [],
            "n_chapters":       n_chapters,
            "prompt_version":   versions.PROMPT_VERSION,
            "deployment_usage": [],
            "error":            "all_samples_failed_after_retry",
            "sample_errors":    sample_errors,
        }
        await minio.write(
            cache_key, json.dumps(payload),
            content_type = "application/json",
        )
        logger.warning(
            f"[order_chapters] {slug}: all {params.N_SAMPLES} samples failed even "
            f"after retry ({sample_errors}); identity ordering applied"
        )
        await domains.dd.planner.runtime.progress.service.emit_progress(
            thread_id, "order_chapters", "done",
            n_chapters = n_chapters, wall_ms = elapsed,
            error = "all_samples_failed_after_retry", sample_errors = sample_errors,
        )
        return {
            "chapter_order_ref": cache_key,
            "order_chapters_stats": {
                "n_chapters":     n_chapters, "wall_ms": elapsed,
                "store_path":     cache_key, "cache_hit": False,
                "order":          identity, "foundational": [],
                "error":          "all_samples_failed_after_retry",
                "sample_errors":  sample_errors,
            },
        }

    rankings = list(valid_orderings)
    if source_ranking:
        rankings += [source_ranking] * params.SOURCE_ORDER_WEIGHT
    aggregated = domain.borda_aggregate(rankings, n_chapters)

    final_order, foundational_idx = domain.apply_foundational_prefix_rule(
        aggregated, chapters,
    )
    final_order, n_sibling_moved = domain.group_siblings(final_order, chapters)
    if n_sibling_moved:
        logger.info(f"[order_chapters] {slug}: kept sibling chapters (`Family: part`) together ({n_sibling_moved} moved)")
    if key_dir:
        final_order, n_home_moved = domain.group_by_home_dir(final_order, chapters, domain.chapter_home_dirs(chapters, key_dir))
        if n_home_moved:
            logger.info(f"[order_chapters] {slug}: kept chapters of the same page directory together ({n_home_moved} moved)")
    # The Foundations chapter (overview + cross-cutting pages) opens the book, ahead
    # of install/setup chapters.
    final_order, foundations_idx = domain.pin_foundations_first(final_order, chapters)
    # Migration / upgrade chapters close the book, ahead of the generic appendix.
    final_order, trailing_idx = domain.pin_trailing_last(final_order, chapters)
    final_order, _appendix_idx = domain.pin_appendix_last(final_order, chapters)

    dep_usage: dict[str, int] = {}
    for m in sample_metas:
        dep = (m or {}).get("deployment") or "?"
        dep_usage[dep] = dep_usage.get(dep, 0) + 1
    deployment_summary = [
        {"deployment": dep, "calls": n}
        for dep, n in sorted(dep_usage.items(), key = lambda kv: -kv[1])
    ]

    payload = {
        "order":            final_order,
        "samples":          valid_orderings,
        "aggregated":       aggregated,
        "foundational_idx": foundational_idx,
        "foundations_idx":  foundations_idx,
        "trailing_idx":     trailing_idx,
        "source_order":     source_ranking,
        "composition":      composition,
        "n_chapters":       n_chapters,
        "prompt_version":   versions.PROMPT_VERSION,
        "deployment_usage": deployment_summary,
        "chapter_titles":   [ch.get("title", "?") for ch in chapters],
    }
    await minio.write(
        cache_key, json.dumps(payload, indent = 2),
        content_type = "application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_chapters":       n_chapters,
        "n_samples":        len(valid_orderings),
        "foundational":     foundational_idx,
        "order":            final_order,
        "aggregated":       aggregated,
        "source_order":     source_ranking,
        "foundations":      foundations_idx,
        "wall_ms":          elapsed,
        "store_path":       cache_key,
        "cache_hit":        False,
        "prompt_version":   versions.PROMPT_VERSION,
        "deployment_usage": deployment_summary,
    }
    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "order_chapters", "done",
        n_chapters = n_chapters, n_samples = len(valid_orderings),
        n_foundational = len(foundational_idx), wall_ms = elapsed,
    )
    titles_ordered = [chapters[i].get("title", "?") for i in final_order]
    logger.info(
        f"[order_chapters] {slug}: {n_chapters} chapters ordered "
        f"({len(valid_orderings)}/{params.N_SAMPLES} samples, "
        f"{len(foundational_idx)} foundational-pinned); "
        f"order={final_order}; titles={titles_ordered}; {elapsed} ms"
    )
    return {
        "chapter_order_ref":    cache_key,
        "order_chapters_stats": stats,
    }
