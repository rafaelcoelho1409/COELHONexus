"""order_chapters I/O shell — one LLM sample call (bandit-routed) + the
order_chapters_run orchestration."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from hashlib import sha256

from domains.llm.rotator.chain import chat_judge_bandit_async

from ...ingestion.storage import get_storage
from ..progress import emit_progress
from ..state import PlannerState

from .domain import (
    apply_foundational_prefix_rule,
    borda_aggregate,
    load_outline,
    parse_order_response,
)
from .keys import blob_key
from .params import MAX_TOKENS, N_SAMPLES, SAMPLE_CONCURRENCY, TEMPERATURE
from .prompts import build_order_prompt
from .versions import PROMPT_VERSION


logger = logging.getLogger(__name__)


async def sample_one_ordering(
    sem: asyncio.Semaphore,
    prompt: str,
    n_chapters: int,
) -> tuple[list[int] | None, dict]:
    """One LLM call. Returns (parsed_order_or_None, meta)."""
    async with sem:
        try:
            response, meta = await chat_judge_bandit_async(
                prompt,
                max_tokens = MAX_TOKENS,
                temperature = TEMPERATURE,
            )
        except Exception as e:
            return None, {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    order = parse_order_response(response, n_chapters)
    if order is None:
        return None, {
            **meta,
            "error": "parse_failed",
            "raw": (response or "")[:120],
        }
    return order, meta


async def order_chapters_run(state: PlannerState) -> dict:
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

    mh = sha256(
        f"reduce={reduce_ref}|n={N_SAMPLES}|v={PROMPT_VERSION}".encode("utf-8"),
    ).hexdigest()[:16]
    cache_key = blob_key(slug, mh)
    minio = get_storage()

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
            await emit_progress(
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

    await emit_progress(thread_id, "order_chapters", "start")

    try:
        reduce_text = await minio.read_text(reduce_ref)
        outline = load_outline(reduce_text)
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
            "prompt_version":   PROMPT_VERSION,
            "deployment_usage": [],
            "skipped":          "trivial_n_chapters",
        }
        await minio.write(
            cache_key, json.dumps(payload),
            content_type = "application/json",
        )
        await emit_progress(
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

    prompt = build_order_prompt(chapters)
    sem = asyncio.Semaphore(SAMPLE_CONCURRENCY)
    sample_results = await asyncio.gather(*[
        sample_one_ordering(sem, prompt, n_chapters)
        for _ in range(N_SAMPLES)
    ])
    valid_orderings = [r[0] for r in sample_results if r[0] is not None]
    sample_metas = [r[1] for r in sample_results]

    await emit_progress(
        thread_id, "order_chapters", "samples_done",
        n_samples = N_SAMPLES, n_valid = len(valid_orderings),
        n_failed = N_SAMPLES - len(valid_orderings),
    )

    if not valid_orderings:
        # All samples failed. Fall back to identity order — better than
        # refusing to ship.
        elapsed = int((time.monotonic() - t0) * 1000)
        identity = list(range(n_chapters))
        payload = {
            "order":            identity,
            "samples":          [],
            "foundational_idx": [],
            "n_chapters":       n_chapters,
            "prompt_version":   PROMPT_VERSION,
            "deployment_usage": [],
            "error":            "all_samples_failed",
        }
        await minio.write(
            cache_key, json.dumps(payload),
            content_type = "application/json",
        )
        logger.warning(
            f"[order_chapters] {slug}: all {N_SAMPLES} samples failed; "
            f"identity ordering applied"
        )
        await emit_progress(
            thread_id, "order_chapters", "done",
            n_chapters = n_chapters, wall_ms = elapsed,
            error = "all_samples_failed",
        )
        return {
            "chapter_order_ref": cache_key,
            "order_chapters_stats": {
                "n_chapters":     n_chapters, "wall_ms": elapsed,
                "store_path":     cache_key, "cache_hit": False,
                "order":          identity, "foundational": [],
                "error":          "all_samples_failed",
            },
        }

    aggregated = borda_aggregate(valid_orderings, n_chapters)

    final_order, foundational_idx = apply_foundational_prefix_rule(
        aggregated, chapters,
    )

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
        "n_chapters":       n_chapters,
        "prompt_version":   PROMPT_VERSION,
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
        "wall_ms":          elapsed,
        "store_path":       cache_key,
        "cache_hit":        False,
        "prompt_version":   PROMPT_VERSION,
        "deployment_usage": deployment_summary,
    }
    await emit_progress(
        thread_id, "order_chapters", "done",
        n_chapters = n_chapters, n_samples = len(valid_orderings),
        n_foundational = len(foundational_idx), wall_ms = elapsed,
    )
    titles_ordered = [chapters[i].get("title", "?") for i in final_order]
    logger.info(
        f"[order_chapters] {slug}: {n_chapters} chapters ordered "
        f"({len(valid_orderings)}/{N_SAMPLES} samples, "
        f"{len(foundational_idx)} foundational-pinned); "
        f"order={final_order}; titles={titles_ordered}; {elapsed} ms"
    )
    return {
        "chapter_order_ref":    cache_key,
        "order_chapters_stats": stats,
    }
