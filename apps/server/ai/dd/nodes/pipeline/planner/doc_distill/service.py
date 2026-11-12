"""doc_distill steps — the nodes of its subgraph: prepare, one distiller per page, finalize.

Every page is distilled by a page_distiller agent into a summary, key terms and a role. A page whose distill fails keeps a deterministic
distillate built from its heading and identifiers (the doc is kept, not dropped). Distillates are stored per page (key + stored size),
so a run that stops resumes with the pages it has not distilled and a re-plan re-distills only the pages that changed."""
from __future__ import annotations
import domains
from . import domain, params, versions
from ..... import agents, graphs

import json
import logging
import time
import typing

import langgraph.runtime


logger = logging.getLogger(__name__)

Context = graphs.pipeline.planner.entities.PlannerContext


def _request(framework: str, source_key: str, body: str, max_chars: int) -> str:
    return json.dumps(
        {"task": "Distill this one documentation file for chapter planning.",
         "framework": framework, "source_key": source_key, "page": body[:max_chars]},
        ensure_ascii = False,
    )


async def prepare(state: graphs.pipeline.planner.doc_distill.states.DistillState, runtime: langgraph.runtime.Runtime[Context]) -> dict:
    blobs = runtime.context.blobs
    slug = state.get("framework_slug")
    relevant_files = state.get("relevant_files") or state.get("raw_files") or []
    if not slug or not relevant_files:
        return {"finished": True, "doc_distill_ref": None, "doc_distill_stats": {"skipped": "no_files", "n_files": 0}}

    n = len(relevant_files)
    started = time.monotonic()
    graphs.service.emit(params.STEP, "start", n_files = n)

    entries: list[dict] = []
    try:
        entries = ((await domains.dd.ingestion.storage.service.read_framework_manifest(blobs, slug)) or {}).get("entries") or []
    except Exception:
        pass
    sizes = {e.get("key"): e.get("bytes") for e in entries if e.get("key")}
    manifest = domain.manifest_hash(
        slug = slug,
        relevant_files = relevant_files,
        content_fp = domain.corpus_content_fingerprint(relevant_files, entries),
    )
    vkey = domains.dd.pipeline.storage.keys.distill_versioned_key(slug, manifest)
    lkey = domains.dd.pipeline.storage.keys.distill_latest_key(slug)
    if await blobs.exists(vkey) and await blobs.exists(lkey):
        try:
            cached = json.loads(await blobs.read_text(vkey))
            wall_ms = graphs.service.elapsed_ms(started)
            stats = {
                "n_files": n,
                "n_distilled": len((cached or {}).get("distillates") or {}),
                "manifest_hash": manifest,
                "cache_hit": True,
                "wall_ms": wall_ms,
            }
            graphs.service.emit(params.STEP, "done", cache_hit = True, n_distilled = stats["n_distilled"], wall_ms = wall_ms)
            return {"finished": True, "doc_distill_ref": lkey, "doc_distill_stats": stats}
        except Exception:
            pass

    digests = {
        key: domains.dd.pipeline.storage.domain.digest(versions.PROMPT_VERSION, key, str(sizes.get(key))) for key in relevant_files
    }
    items = await domains.dd.pipeline.storage.service.read_items(blobs, slug, params.STEP, versions.PROMPT_VERSION, list(digests.values()))
    cached_items = {key: items[digest] for key, digest in digests.items() if digest in items}
    todo = [key for key in relevant_files if key not in cached_items]
    if todo:
        await graphs.service.settle(params.STEP, params.SETTLE_DELAY_S)
    runtime.context.counters[params.STEP] = {"distilled": 0}
    logger.info(f"[doc_distill] {slug}: {n} pages ({len(cached_items)} distilled by an earlier run, {len(todo)} to distill)")
    return {
        "finished": False,
        "manifest_hash": manifest,
        "digests": digests,
        "cached": cached_items,
        "todo": todo,
        "started": started,
    }


async def distill_page(state: graphs.pipeline.planner.doc_distill.entities.DistillInput, runtime: langgraph.runtime.Runtime[Context]) -> dict:
    """Distill one page. Returns its distillate (the model's, normalized), or a deterministic fallback with the reason the model gave none."""
    context = runtime.context
    key = state["key"]
    result: dict[str, typing.Any]
    try:
        body: str | None = await context.blobs.read_text(key)
    except Exception:
        body = None
    if body is None:
        result = {"key": key, "distillate": None, "fallback": False, "reason": "read_fail"}
    elif not body.strip():
        result = {"key": key, "distillate": None, "fallback": False, "reason": "empty_body"}
    else:
        distillate = None
        reason = None
        error = ""
        for attempt in range(params.ATTEMPTS):
            max_chars = params.BODY_CHARS_MAX // (params.BODY_CHARS_RETRY_DIVISOR if attempt else 1)
            try:
                raw = await agents.service.ask(
                    context.agents.page_distiller(),
                    agents.pipeline.planner.schemas.DocDistillateRaw,
                    _request(state["framework_slug"], key, body, max_chars),
                    attempts = params.ASK_ATTEMPTS,
                    wait_base_s = params.RETRY_WAIT_S,
                )
                distillate = domain.normalize_distillate(raw.summary, raw.key_terms, key, body, raw.role)
                reason = None
                break
            except Exception as exc:
                reason = domain.classify_error(exc)
                error = f"{type(exc).__name__}: {str(exc)[:160]}"
                logger.warning(f"[doc_distill] {key} attempt {attempt + 1}: {reason} ({error})")
        if distillate is not None:
            result = {"key": key, "distillate": distillate.model_dump(), "fallback": False}
            try:
                await domains.dd.pipeline.storage.service.write_item(
                    context.blobs, state["framework_slug"], params.STEP, versions.PROMPT_VERSION, state["digest"], result["distillate"],
                )
            except Exception as exc:
                logger.warning(f"[doc_distill] distillate not stored ({type(exc).__name__}: {exc})")
        else:
            logger.info(f"[doc_distill] {key}: distill failed ({reason}, {error[:120]}) — using a deterministic fallback distillate (doc kept)")
            result = {
                "key": key,
                "distillate": domain.build_fallback_distillate(key, body).model_dump(),
                "fallback": True,
                "reason": reason or "unknown",
            }

    counts = context.counters.setdefault(params.STEP, {"distilled": 0})
    counts["distilled"] += 1
    every = max(1, state["total"] // params.PROGRESS_EVENTS)
    if counts["distilled"] % every == 0 or counts["distilled"] == state["total"]:
        graphs.service.emit(params.STEP, "llm_progress", distilled = counts["distilled"], total = state["total"])
    return {"results": [result]}


async def finalize(state: graphs.pipeline.planner.doc_distill.states.DistillState, runtime: langgraph.runtime.Runtime[Context]) -> dict:
    """Assemble the node's blob (every page's distillate) and its stats; write the versioned blob and the latest pointer."""
    blobs = runtime.context.blobs
    slug = state["framework_slug"]
    relevant_files = state.get("relevant_files") or state.get("raw_files") or []
    manifest = state["manifest_hash"]
    n = len(relevant_files)

    distillates: dict[str, dict] = dict(state.get("cached") or {})
    failures: list[dict] = []     # no content at all (read fail, empty page)
    fallbacks: list[dict] = []    # content, but the model gave no usable distillate
    failure_reasons: dict[str, int] = {}
    for result in state.get("results") or []:
        reason = result.get("reason")
        if result["distillate"] is not None:
            distillates[result["key"]] = result["distillate"]
        if result["fallback"]:
            fallbacks.append({"key": result["key"], "reason": reason or "unknown"})
        elif result["distillate"] is None:
            failures.append({"key": result["key"], "reason": reason or "unknown"})
        if reason:
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

    for reason in {fb["reason"] for fb in fallbacks}:
        keys = [fb["key"] for fb in fallbacks if fb["reason"] == reason]
        logger.warning(f"[doc_distill] {slug}: {len(keys)} doc(s) fell back due to {reason}: {keys[:10]}")

    ordered = {key: distillates[key] for key in relevant_files if key in distillates}
    lkey = domains.dd.pipeline.storage.keys.distill_latest_key(slug)
    await domains.dd.pipeline.storage.service.write_blob(
        blobs,
        domains.dd.pipeline.storage.keys.distill_versioned_key(slug, manifest),
        lkey,
        {
            "prompt_version": versions.PROMPT_VERSION,
            "manifest_hash": manifest,
            "framework_slug": slug,
            "distillates": ordered,
            "n_files": n,
            "n_distilled": len(ordered),
            "n_failed": len(failures),
            "failures": failures[:20],
            "n_fallback": len(fallbacks),
            "fallbacks": fallbacks[:20],
            "failure_reasons": failure_reasons,
        },
    )
    wall_ms = graphs.service.elapsed_ms(state.get("started") or time.monotonic())
    stats = {
        "n_files": n,
        "n_distilled": len(ordered),
        "n_failed": len(failures),
        "n_fallback": len(fallbacks),
        "failure_reasons": failure_reasons,
        "manifest_hash": manifest,
        "cache_hit": False,
        "wall_ms": wall_ms,
        "read_ms": 0,
        "n_cached": len(state.get("cached") or {}),
    }
    graphs.service.emit(
        params.STEP, "done",
        cache_hit = False, n_distilled = len(ordered), n_failed = len(failures), n_fallback = len(fallbacks),
        failure_reasons = failure_reasons, wall_ms = wall_ms,
    )
    return {"doc_distill_ref": lkey, "doc_distill_stats": stats}
