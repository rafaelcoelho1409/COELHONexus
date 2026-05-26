"""Substep 7.5 — order_chapters: pedagogically sort chapters before plan_write.

Bundle 8 (2026-05-25). Replaces the arbitrary HDBSCAN-cluster-id ordering
from reduce with an LLM-driven pedagogical ordering (USC vote + Borda
aggregation) and a deterministic foundational-prefix rule.

State reads:
  chapter_plan_ref  — from reduce; load outline.chapters list
State writes:
  chapter_order_ref     — MinIO key of the JSON ordering blob
  order_chapters_stats  — observability dict (samples + winner + telemetry)

Persisted JSON shape:
  {
    "order":              [<chapter_idx>, ...],   # final pedagogical order
    "samples":            [[idx, ...], ...],      # N raw rankings from LLM
    "foundational_idx":   [<idx>, ...],           # chapters pinned to front
    "n_chapters":         <int>,
    "prompt_version":     <str>,
    "deployment_usage":   [{"deployment": <str>, "calls": <int>}, ...]
  }
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from hashlib import sha256

from ...ingestion.storage import get_storage

from ..observability.spans import traced
from ..progress import emit_progress
from ..reduce import load_outline
from ..state import PlannerState

from .constants import (
    _N_SAMPLES,
    _PROMPT_VERSION,
    _SAMPLE_CONCURRENCY,
)
from .service import (
    _blob_key,
    _sample_one_ordering,
    apply_foundational_prefix_rule,
    borda_aggregate,
    build_order_prompt,
)


logger = logging.getLogger(__name__)


@traced("order_chapters")
async def order_chapters(state: PlannerState) -> dict:
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

    # ── Cache fast-path ────────────────────────────────────────────────
    mh = sha256(
        (f"reduce={reduce_ref}|n={_N_SAMPLES}|v={_PROMPT_VERSION}").encode("utf-8"),
    ).hexdigest()[:16]
    blob_key = _blob_key(slug, mh)
    minio = get_storage()

    if await minio.exists(blob_key):
        try:
            cached = json.loads(await minio.read_text(blob_key))
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_chapters":    cached.get("n_chapters", 0),
                "n_samples":     len(cached.get("samples") or []),
                "foundational":  cached.get("foundational_idx") or [],
                "order":         cached.get("order") or [],
                "wall_ms":       elapsed,
                "store_path":    blob_key,
                "cache_hit":     True,
                "prompt_version": cached.get("prompt_version"),
            }
            await emit_progress(
                thread_id, "order_chapters", "done",
                n_chapters=stats["n_chapters"], wall_ms=elapsed,
                cache_hit=True,
            )
            logger.info(
                f"[order_chapters] {slug}: CACHE HIT — "
                f"order={stats['order']}, foundational="
                f"{stats['foundational']}, {elapsed} ms"
            )
            return {
                "chapter_order_ref": blob_key,
                "order_chapters_stats": stats,
            }
        except Exception as e:
            logger.warning(
                f"[order_chapters] {slug}: cached blob {blob_key!r} "
                f"unreadable ({type(e).__name__}: {e}); recomputing"
            )

    await emit_progress(thread_id, "order_chapters", "start")

    # ── Load reduce outline ───────────────────────────────────────────
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
        # 0 or 1 chapter — no meaningful ordering. Persist trivial blob so
        # downstream plan_write sees the file and skips its own reorder.
        elapsed = int((time.monotonic() - t0) * 1000)
        identity = list(range(n_chapters))
        payload = {
            "order":            identity,
            "samples":          [],
            "foundational_idx": [],
            "n_chapters":       n_chapters,
            "prompt_version":   _PROMPT_VERSION,
            "deployment_usage": [],
            "skipped":          "trivial_n_chapters",
        }
        await minio.write(
            blob_key, json.dumps(payload), content_type="application/json",
        )
        await emit_progress(
            thread_id, "order_chapters", "done",
            n_chapters=n_chapters, wall_ms=elapsed,
            skipped="trivial_n_chapters",
        )
        return {
            "chapter_order_ref": blob_key,
            "order_chapters_stats": {
                "n_chapters": n_chapters, "wall_ms": elapsed,
                "store_path": blob_key, "cache_hit": False,
                "order": identity, "foundational": [],
                "skipped": "trivial_n_chapters",
            },
        }

    # ── Build prompt + sample N orderings in parallel ──────────────────
    prompt = build_order_prompt(chapters)
    sem = asyncio.Semaphore(_SAMPLE_CONCURRENCY)
    sample_results = await asyncio.gather(*[
        _sample_one_ordering(sem, prompt, n_chapters)
        for _ in range(_N_SAMPLES)
    ])
    valid_orderings = [r[0] for r in sample_results if r[0] is not None]
    sample_metas = [r[1] for r in sample_results]

    await emit_progress(
        thread_id, "order_chapters", "samples_done",
        n_samples=_N_SAMPLES, n_valid=len(valid_orderings),
        n_failed=_N_SAMPLES - len(valid_orderings),
    )

    if not valid_orderings:
        # All samples failed parsing / LLM calls. Fall back to identity order
        # (preserves what reduce emitted) — better than refusing to ship.
        elapsed = int((time.monotonic() - t0) * 1000)
        identity = list(range(n_chapters))
        payload = {
            "order":            identity,
            "samples":          [],
            "foundational_idx": [],
            "n_chapters":       n_chapters,
            "prompt_version":   _PROMPT_VERSION,
            "deployment_usage": [],
            "error":            "all_samples_failed",
        }
        await minio.write(
            blob_key, json.dumps(payload), content_type="application/json",
        )
        logger.warning(
            f"[order_chapters] {slug}: all {_N_SAMPLES} samples failed; "
            f"identity ordering applied"
        )
        await emit_progress(
            thread_id, "order_chapters", "done",
            n_chapters=n_chapters, wall_ms=elapsed,
            error="all_samples_failed",
        )
        return {
            "chapter_order_ref": blob_key,
            "order_chapters_stats": {
                "n_chapters":     n_chapters, "wall_ms": elapsed,
                "store_path":     blob_key, "cache_hit": False,
                "order":          identity, "foundational": [],
                "error":          "all_samples_failed",
            },
        }

    # ── Borda aggregate ────────────────────────────────────────────────
    aggregated = borda_aggregate(valid_orderings, n_chapters)

    # ── Foundational-prefix rule (deterministic override) ──────────────
    final_order, foundational_idx = apply_foundational_prefix_rule(
        aggregated, chapters,
    )

    # ── Deployment telemetry ───────────────────────────────────────────
    dep_usage: dict[str, int] = {}
    for m in sample_metas:
        dep = (m or {}).get("deployment") or "?"
        dep_usage[dep] = dep_usage.get(dep, 0) + 1
    deployment_summary = [
        {"deployment": dep, "calls": n}
        for dep, n in sorted(dep_usage.items(), key=lambda kv: -kv[1])
    ]

    # ── Persist ────────────────────────────────────────────────────────
    payload = {
        "order":            final_order,
        "samples":          valid_orderings,
        "aggregated":       aggregated,
        "foundational_idx": foundational_idx,
        "n_chapters":       n_chapters,
        "prompt_version":   _PROMPT_VERSION,
        "deployment_usage": deployment_summary,
        "chapter_titles":   [ch.get("title", "?") for ch in chapters],
    }
    await minio.write(
        blob_key, json.dumps(payload, indent=2), content_type="application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_chapters":       n_chapters,
        "n_samples":        len(valid_orderings),
        "foundational":     foundational_idx,
        "order":            final_order,
        "aggregated":       aggregated,
        "wall_ms":          elapsed,
        "store_path":       blob_key,
        "cache_hit":        False,
        "prompt_version":   _PROMPT_VERSION,
        "deployment_usage": deployment_summary,
    }
    await emit_progress(
        thread_id, "order_chapters", "done",
        n_chapters=n_chapters, n_samples=len(valid_orderings),
        n_foundational=len(foundational_idx), wall_ms=elapsed,
    )
    # Compact log line with the actual ordering for operator visibility.
    titles_ordered = [chapters[i].get("title", "?") for i in final_order]
    logger.info(
        f"[order_chapters] {slug}: {n_chapters} chapters ordered "
        f"({len(valid_orderings)}/{_N_SAMPLES} samples, "
        f"{len(foundational_idx)} foundational-pinned); "
        f"order={final_order}; titles={titles_ordered}; {elapsed} ms"
    )
    return {
        "chapter_order_ref":    blob_key,
        "order_chapters_stats": stats,
    }
