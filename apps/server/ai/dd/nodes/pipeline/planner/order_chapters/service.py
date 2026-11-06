"""order_chapters steps — the nodes of its subgraph: prepare, one sequencer sample per branch, collect, and the Borda aggregate with the deterministic pins."""
from __future__ import annotations
import domains
from . import domain, params, versions
from .. import chapter_select, shared
from ..... import agents, graphs

import json
import logging
import time
from hashlib import sha256

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)

Context = graphs.pipeline.planner.context.PlannerContext


def _blob(slug: str, mh: str) -> str:
    return domains.dd.pipeline.storage.keys.order_key(slug, mh)


async def prepare(state: graphs.pipeline.planner.order_chapters.state.OrderState, runtime: Runtime[Context]) -> dict:
    """Answer from the cache or by rule when possible; otherwise load what the samples need (chapters, composition, source order)."""
    blobs = runtime.context.blobs
    slug = state.get("framework_slug") or ""
    reduce_ref = state.get("chapter_plan_ref") or ""
    started = time.monotonic()
    if not slug or not reduce_ref:
        return {"finished": True, "chapter_order_ref": "", "order_chapters_stats": {"skipped": "no_input", "n_chapters": 0, "wall_ms": 0}}

    # Identity follows the chapter plan's CONTENT: `reduce_ref` is the constant "latest" path, so hashing it alone served the
    # previous run's order for a different chapter set (asyncio: the same order file for a 12- and a 10-chapter plan).
    try:
        content = await blobs.read_text(reduce_ref)
    except Exception:
        content = ""
    mh = sha256(
        f"reduce={reduce_ref}#{sha256(content.encode('utf-8')).hexdigest()[:16]}|n={params.N_SAMPLES}|v={versions.PROMPT_VERSION}".encode("utf-8"),
    ).hexdigest()[:16]
    cache_key = _blob(slug, mh)

    if await blobs.exists(cache_key):
        try:
            cached = json.loads(await blobs.read_text(cache_key))
            elapsed = shared.service.elapsed_ms(started)
            stats = {
                "n_chapters": cached.get("n_chapters", 0),
                "n_samples": len(cached.get("samples") or []),
                "foundational": cached.get("foundational_idx") or [],
                "order": cached.get("order") or [],
                "wall_ms": elapsed,
                "store_path": cache_key,
                "cache_hit": True,
                "prompt_version": cached.get("prompt_version"),
            }
            shared.service.emit("order_chapters", "done", n_chapters = stats["n_chapters"], wall_ms = elapsed, cache_hit = True)
            logger.info(f"[order_chapters] {slug}: CACHE HIT — order={stats['order']}, foundational={stats['foundational']}, {elapsed} ms")
            return {"finished": True, "chapter_order_ref": cache_key, "order_chapters_stats": stats}
        except Exception as error:
            logger.warning(f"[order_chapters] {slug}: cached blob {cache_key!r} unreadable ({type(error).__name__}: {error}); recomputing")

    shared.service.emit("order_chapters", "start")
    try:
        outline = domain.load_outline(content or await blobs.read_text(reduce_ref))
    except Exception as error:
        logger.warning(f"[order_chapters] {slug}: reduce outline unreadable ({type(error).__name__}: {error}); identity order")
        return {
            "finished": True,
            "chapter_order_ref": "",
            "order_chapters_stats": {
                "skipped": "outline_unreadable",
                "error": f"{type(error).__name__}: {str(error)[:120]}",
                "wall_ms": shared.service.elapsed_ms(started),
            },
        }
    chapters = (outline or {}).get("chapters") or []
    n_chapters = len(chapters)

    if n_chapters <= 1:
        # 0 or 1 chapter: no meaningful ordering. Persist a trivial blob so plan_write sees the file and skips its own reorder.
        elapsed = shared.service.elapsed_ms(started)
        identity = list(range(n_chapters))
        await blobs.write(
            cache_key,
            json.dumps({
                "order": identity, "samples": [], "foundational_idx": [], "n_chapters": n_chapters,
                "prompt_version": versions.PROMPT_VERSION, "deployment_usage": [], "skipped": "trivial_n_chapters",
            }),
            content_type = "application/json",
        )
        shared.service.emit("order_chapters", "done", n_chapters = n_chapters, wall_ms = elapsed, skipped = "trivial_n_chapters")
        return {
            "finished": True,
            "chapter_order_ref": cache_key,
            "order_chapters_stats": {
                "n_chapters": n_chapters, "wall_ms": elapsed, "store_path": cache_key, "cache_hit": False,
                "order": identity, "foundational": [], "skipped": "trivial_n_chapters",
            },
        }

    # Source order: where each chapter's pages sit in the docs' own navigation.
    source_ranking: list[int] | None = None
    homes: list[list[str] | None] = [None] * n_chapters
    try:
        manifest = await domains.dd.ingestion.storage.service.read_framework_manifest(blobs, slug)
        entries = (manifest or {}).get("entries") or []
        key_dir = chapter_select.domain.page_dirs(entries)
        homes = [list(home) if home else None for home in domain.chapter_home_dirs(chapters, key_dir)]
        if domain.source_order_informative(entries):
            index = {e["key"]: int(e.get("idx") or 0) for e in entries if e.get("key")}
            source_ranking = domain.source_order_ranking(chapters, index)
        else:
            logger.info(f"[order_chapters] {slug}: source order not informative (single URL or alphabetical) — LLM votes only")
    except Exception as error:
        logger.info(f"[order_chapters] {slug}: source order unavailable ({type(error).__name__})")

    # Only reached past the cache and trivial checks, so a fully cached re-plan never pays the wait.
    await shared.service.settle("order_chapters", params.SETTLE_DELAY_S)

    composition = None
    try:
        distillates = await domains.dd.pipeline.storage.service.load_distillates(blobs, slug)
        composition = domain.chapter_composition(chapters, distillates or {})
    except Exception as error:
        logger.info(f"[order_chapters] {slug}: chapter composition unavailable ({type(error).__name__})")

    return {
        "finished": False,
        "chapters": chapters,
        "composition": composition,
        "source_ranking": source_ranking,
        "homes": homes,
        "cache_key": cache_key,
        "started": started,
        "retry_done": False,
        "retry_samples": [],
    }


async def sample_order(state: graphs.pipeline.planner.order_chapters.state.SampleInput, runtime: Runtime[Context]) -> dict:
    """One sequencer sample: the model's chapter titles in reading order, mapped back to chapter indexes."""
    chapters = state["chapters"]
    labels = domain.chapter_labels(chapters)
    index = state["sample_idx"]
    payload = domain.order_payload(chapters, state.get("composition"), state["framework_slug"], index)
    try:
        answer = await agents.service.ask(
            runtime.context.agents.chapter_sequencer(),
            agents.pipeline.planner.schemas.ChapterTitleOrder,
            payload,
            attempts = params.SAMPLE_ATTEMPTS,
            wait_base_s = params.SAMPLE_RETRY_WAIT_S,
            check = lambda value: domain.titles_to_order(value.order, labels) is not None,
        )
    except Exception as error:
        return {"samples": [{"sample": index, "order": None, "error": f"{type(error).__name__}: {str(error)[:120]}"}]}
    order = domain.titles_to_order(answer.order, labels)
    if order is None:
        return {"samples": [{"sample": index, "order": None, "error": f"parse_failed_after_reask: {str(answer.order)[:100]}"}]}
    return {"samples": [{"sample": index, "order": order}]}


def collect(state: graphs.pipeline.planner.order_chapters.state.OrderState) -> dict:
    """After a round of samples: with fewer valid orderings than MIN_VALID_SAMPLES, ask once more for the missing ones."""
    samples = state.get("samples") or []
    valid = [s for s in samples if s["order"] is not None]
    shared.service.emit(
        "order_chapters", "samples_done",
        n_samples = len(samples), n_valid = len(valid), n_failed = len(samples) - len(valid),
    )
    if state.get("retry_done") or len(valid) >= params.MIN_VALID_SAMPLES:
        return {"retry_samples": []}
    # Fewer than MIN_VALID_SAMPLES valid (langfuse 2026-10-14, a slow provider for an hour: 1 of 5 valid, so the source order
    # outvoted it 2:1): ask again for the missing ones after a jittered backoff, so the calls do not hit the same
    # still-recovering deployment in lockstep.
    missing = params.N_SAMPLES - len(valid)
    logger.warning(
        f"[order_chapters] {len(valid)}/{params.N_SAMPLES} samples valid "
        f"({[s['error'] for s in samples if s.get('error')]}) — asking for the missing ones once more"
    )
    return {"retry_samples": [params.N_SAMPLES + i for i in range(missing)], "retry_done": True}


async def finalize(state: graphs.pipeline.planner.order_chapters.state.OrderState, runtime: Runtime[Context]) -> dict:
    """Aggregate the valid orderings (and the docs' own order) by Borda, apply the pins, persist the blob and the stats."""
    blobs = runtime.context.blobs
    slug = state["framework_slug"]
    chapters = state["chapters"]
    cache_key = state["cache_key"]
    n_chapters = len(chapters)
    samples = state.get("samples") or []
    valid_orderings = [s["order"] for s in samples if s["order"] is not None]
    source_ranking = state.get("source_ranking")
    elapsed = shared.service.elapsed_ms(state.get("started") or time.monotonic())

    if not valid_orderings:
        # Even the retry failed: the identity order (the docs' own when known), pinned. Better than refusing to ship; each
        # sample's error is persisted so a repeat of this failure is diagnosable from the stored blob.
        identity = list(source_ranking) if source_ranking else list(range(n_chapters))
        identity, _ = domain.pin_foundations_first(identity, chapters)
        identity, _ = domain.pin_trailing_last(identity, chapters)
        identity, _ = domain.pin_appendix_last(identity, chapters)
        sample_errors = [s.get("error", "unknown") for s in samples]
        await blobs.write(
            cache_key,
            json.dumps({
                "order": identity, "samples": [], "foundational_idx": [], "n_chapters": n_chapters,
                "prompt_version": versions.PROMPT_VERSION, "deployment_usage": [],
                "error": "all_samples_failed_after_retry", "sample_errors": sample_errors,
            }),
            content_type = "application/json",
        )
        logger.warning(f"[order_chapters] {slug}: all {len(samples)} samples failed even after retry ({sample_errors}); identity ordering applied")
        shared.service.emit(
            "order_chapters", "done", n_chapters = n_chapters, wall_ms = elapsed,
            error = "all_samples_failed_after_retry", sample_errors = sample_errors,
        )
        return {
            "chapter_order_ref": cache_key,
            "order_chapters_stats": {
                "n_chapters": n_chapters, "wall_ms": elapsed, "store_path": cache_key, "cache_hit": False,
                "order": identity, "foundational": [], "error": "all_samples_failed_after_retry", "sample_errors": sample_errors,
            },
        }

    rankings = list(valid_orderings)
    if source_ranking:
        rankings += [source_ranking] * params.SOURCE_ORDER_WEIGHT
    aggregated = domain.borda_aggregate(rankings, n_chapters)
    final_order, foundational_idx = domain.apply_foundational_prefix_rule(aggregated, chapters)
    final_order, n_sibling_moved = domain.group_siblings(final_order, chapters)
    if n_sibling_moved:
        logger.info(f"[order_chapters] {slug}: kept sibling chapters (`Family: part`) together ({n_sibling_moved} moved)")
    homes = [tuple(home) if home else None for home in state.get("homes") or []]
    if any(homes):
        final_order, n_home_moved = domain.group_by_home_dir(final_order, chapters, homes)
        if n_home_moved:
            logger.info(f"[order_chapters] {slug}: kept chapters of the same page directory together ({n_home_moved} moved)")
    # The Foundations chapter (overview + cross-cutting pages) opens the book, ahead of install/setup chapters; migration and
    # upgrade chapters close it, ahead of the generic appendix.
    final_order, foundations_idx = domain.pin_foundations_first(final_order, chapters)
    final_order, trailing_idx = domain.pin_trailing_last(final_order, chapters)
    final_order, _appendix_idx = domain.pin_appendix_last(final_order, chapters)

    await blobs.write(
        cache_key,
        json.dumps({
            "order": final_order, "samples": valid_orderings, "aggregated": aggregated,
            "foundational_idx": foundational_idx, "foundations_idx": foundations_idx, "trailing_idx": trailing_idx,
            "source_order": source_ranking, "composition": state.get("composition"), "n_chapters": n_chapters,
            "prompt_version": versions.PROMPT_VERSION, "deployment_usage": [],
            "chapter_titles": [ch.get("title", "?") for ch in chapters],
        }, indent = 2),
        content_type = "application/json",
    )
    stats = {
        "n_chapters": n_chapters,
        "n_samples": len(valid_orderings),
        "foundational": foundational_idx,
        "order": final_order,
        "aggregated": aggregated,
        "source_order": source_ranking,
        "foundations": foundations_idx,
        "wall_ms": elapsed,
        "store_path": cache_key,
        "cache_hit": False,
        "prompt_version": versions.PROMPT_VERSION,
        "deployment_usage": [],
    }
    shared.service.emit(
        "order_chapters", "done",
        n_chapters = n_chapters, n_samples = len(valid_orderings), n_foundational = len(foundational_idx), wall_ms = elapsed,
    )
    logger.info(
        f"[order_chapters] {slug}: {n_chapters} chapters ordered ({len(valid_orderings)}/{params.N_SAMPLES} samples, "
        f"{len(foundational_idx)} foundational-pinned); order={final_order}; "
        f"titles={[chapters[i].get('title', '?') for i in final_order]}; {elapsed} ms"
    )
    return {"chapter_order_ref": cache_key, "order_chapters_stats": stats}
