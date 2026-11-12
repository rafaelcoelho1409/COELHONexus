"""off_topic steps — the nodes of its subgraph: prepare, one judge per distinct page text, tally, finalize.

A KEEP/DROP judge per page, LLM-only (no embeddings):
- Pages whose judged text is identical (empty pages, stub redirects, scaffold duplicates, mirrored dumps) share ONE call.
- Reference sub-pages (one per documented class or function) are the framework's own API docs, on-topic by construction: never judged.
- A verdict is stored per distinct text, so a run that stops resumes with the pages it has not judged, and a re-plan of an
  unchanged corpus judges nothing.
- A confident DROP of a substantial page needs a second DROP: one free-tier call is noisy (fastmcp: 4 drops one run, 15 the
  next, one a real OAuth guide) and a dropped guide is silently missing from the book. KEEP wins; a judge error already means KEEP.
"""
from __future__ import annotations
import domains
from . import domain, params, versions
from ..... import agents, graphs

import logging
import time

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)

Context = graphs.pipeline.planner.entities.PlannerContext


async def prepare(state: graphs.pipeline.planner.off_topic.states.OffTopicState, runtime: Runtime[Context]) -> dict:
    blobs = runtime.context.blobs
    slug = state.get("framework_slug")
    raw_files = state.get("raw_files") or []
    if not slug or not raw_files:
        return {
            "finished": True,
            "relevant_files": list(raw_files),
            "off_topic_stats": {"kept": len(raw_files), "dropped": 0, "skipped": "no input"},
        }

    entry = domains.dd.catalog.service.index_by_slug().get(slug, {})
    framework_name = entry.get("name") or entry.get("slug") or slug
    category = entry.get("category") or ""
    # Tier 2 index summary, read off the manifest by corpus_load ("" for Tier 1 and for manifests that predate it).
    index_summary = (state.get("corpus_stats") or {}).get("index_summary") or ""

    started = time.monotonic()
    graphs.service.emit(params.STEP, "start", files = len(raw_files))
    bodies = await domains.dd.pipeline.storage.service.read_all(blobs, raw_files)

    # Best-effort: without the manifest every page is judged.
    auto_keep: set[int] = set()
    try:
        manifest = await domains.dd.ingestion.storage.service.read_framework_manifest(blobs, slug)
        reference_keys = domain.reference_page_keys((manifest or {}).get("entries") or [])
        auto_keep = {i for i, key in enumerate(raw_files) if key in reference_keys}
    except Exception as error:
        logger.info(f"[off_topic] {slug}: manifest unavailable for reference-page auto-keep ({type(error).__name__})")

    groups: dict[str, list[int]] = {}
    reps: dict[str, str] = {}
    lengths: dict[str, int] = {}
    for i, body in enumerate(bodies):
        if i in auto_keep:
            continue
        judged = domain.head_tail_truncate(body or "")
        digest = domains.dd.pipeline.storage.domain.digest(versions.PROMPT_VERSION, framework_name, category, index_summary, judged)
        groups.setdefault(digest, []).append(i)
        reps.setdefault(digest, raw_files[i])
        lengths[digest] = len(judged)

    cached = await domains.dd.pipeline.storage.service.read_items(blobs, slug, params.STEP, versions.PROMPT_VERSION, list(groups))
    todo = [digest for digest in groups if digest not in cached]
    runtime.context.counters[params.STEP] = {"judged": 0, "keep": 0, "drop": 0, "err": 0}
    logger.info(f"[off_topic] {slug}: {len(raw_files)} pages → {len(groups)} distinct texts ({len(cached)} judged by an earlier run, {len(todo)} to judge)")
    return {
        "finished": False,
        "framework_name": framework_name,
        "category": category,
        "index_summary": index_summary,
        "groups": groups,
        "reps": reps,
        "lengths": lengths,
        "cached": cached,
        "auto_keep": sorted(auto_keep),
        "todo": todo,
        "rechecked": False,
        "recheck": [],
        "started": started,
    }


async def judge_page(state: graphs.pipeline.planner.off_topic.entities.JudgeInput, runtime: Runtime[Context]) -> dict:
    """One judge call for one distinct page text. A call that cannot be answered counts as KEEP (quality over speed)."""
    context = runtime.context
    try:
        body = await context.blobs.read_text(state["key"])
    except Exception:
        body = ""
    page = domain.head_tail_truncate(body)
    error: str | None = None
    try:
        answer = await agents.service.ask(
            context.agents.page_judge(),
            agents.pipeline.planner.schemas.PageRelevance,
            domain.judge_payload(state["framework_name"], state["category"], state["summary"], page),
            attempts = params.JUDGE_MAX_ATTEMPTS,
            wait_base_s = params.JUDGE_RETRY_WAIT_S,
        )
        keep = answer.verdict == "KEEP"
    except Exception as exc:
        keep, error = True, f"{type(exc).__name__}: {str(exc)[:160]}"

    if state["round"] == 1:
        if error is None:
            try:
                await domains.dd.pipeline.storage.service.write_item(
                    context.blobs, state["framework_slug"], params.STEP, versions.PROMPT_VERSION, state["digest"], {"keep": keep, "final": False},
                )
            except Exception as exc:
                logger.warning(f"[off_topic] verdict not stored ({type(exc).__name__}: {exc}); the page is judged again on a resume")
        counts = context.counters.setdefault(params.STEP, {"judged": 0, "keep": 0, "drop": 0, "err": 0})
        counts["judged"] += 1
        counts["err" if error else ("keep" if keep else "drop")] += 1
        every = max(1, state["total"] // params.PROGRESS_EVENTS)
        if counts["judged"] % every == 0 or counts["judged"] == state["total"]:
            graphs.service.emit(
                params.STEP, "llm_progress",
                judged = counts["judged"], total = state["total"], deduped = state["deduped"],
                llm_keep = counts["keep"], llm_drop = counts["drop"], llm_err = counts["err"],
            )
    verdict = {"digest": state["digest"], "keep": keep, "round": state["round"]}
    if error:
        verdict["error"] = error
    return {"verdicts": [verdict]}


def tally(state: graphs.pipeline.planner.off_topic.states.OffTopicState, runtime: Runtime[Context]) -> dict:
    """After round 1: the confident DROPs of substantial pages that a second call must confirm."""
    if state.get("rechecked"):
        return {"recheck": []}
    first = {v["digest"]: v for v in state.get("verdicts") or [] if v["round"] == 1}
    cached = state.get("cached") or {}
    lengths = state.get("lengths") or {}
    recheck = []
    for digest in state.get("groups") or {}:
        if digest in first:
            confident_drop = not first[digest]["keep"] and "error" not in first[digest]
        else:
            confident_drop = digest in cached and not cached[digest].get("keep", True) and not cached[digest].get("final")
        if confident_drop and lengths.get(digest, 0) >= params.RECHECK_MIN_CHARS:
            recheck.append(digest)
    return {"recheck": recheck, "rechecked": True}


async def finalize(state: graphs.pipeline.planner.off_topic.states.OffTopicState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    slug = state["framework_slug"]
    raw_files = state["raw_files"]
    groups = state["groups"]
    cached = state.get("cached") or {}
    auto_keep = set(state.get("auto_keep") or [])
    rounds: dict[int, dict[str, dict]] = {1: {}, 2: {}}
    for verdict in state.get("verdicts") or []:
        rounds[verdict["round"]][verdict["digest"]] = verdict

    final: dict[str, tuple[bool, str | None]] = {}
    rescued = 0
    for digest in groups:
        if digest in rounds[1]:
            first = rounds[1][digest]
            keep, error = first["keep"], first.get("error")
        else:
            keep, error = cached[digest]["keep"], None
        second = rounds[2].get(digest)
        if second is not None and second["keep"]:
            keep, error = True, second.get("error")   # both calls must say DROP; KEEP wins
            rescued += 1
        final[digest] = (keep, error)
        if second is not None:
            try:
                await domains.dd.pipeline.storage.service.write_item(
                    context.blobs, slug, params.STEP, versions.PROMPT_VERSION, digest, {"keep": keep, "final": True},
                )
            except Exception as exc:
                logger.warning(f"[off_topic] final verdict not stored ({type(exc).__name__}: {exc})")

    verdicts: list = [None] * len(raw_files)
    for i in auto_keep:
        verdicts[i] = (True, "AUTO_KEEP", None, {})
    for digest, indexes in groups.items():
        keep, error = final[digest]
        for i in indexes:
            verdicts[i] = (keep, "KEEP" if keep else "DROP", error, {})

    agg = domain.aggregate_verdicts(verdicts = verdicts, raw_files = raw_files)
    relevant = agg["relevant"]
    n = len(raw_files)
    n_judged = len(groups)
    n_deduped = (n - len(auto_keep)) - n_judged
    llm_kept = agg["llm_kept"] - len(auto_keep)   # auto-kept pages carry a KEEP verdict but never reached the judge
    elapsed = graphs.service.elapsed_ms(state.get("started") or time.monotonic())
    entry = domains.dd.catalog.service.index_by_slug().get(slug, {})
    summary = state.get("index_summary") or ""

    stats = {
        "kept":                len(relevant),
        "dropped":             n - len(relevant),
        "total":               n,
        "llm_judged":          n_judged,
        "llm_cached":          len(cached),
        "llm_deduped":         n_deduped,
        "n_auto_kept":         len(auto_keep),
        "llm_kept":            llm_kept,
        "llm_dropped":         agg["llm_dropped"],
        "llm_errors":          len(agg["judge_errors"]),
        "llm_rechecked":       len(rounds[2]),
        "llm_recheck_kept":    rescued,
        "llm_error_breakdown": agg["error_breakdown"],
        "domain_coherence":    0.0,
        "per_file_margins":    agg["per_file"],
        "judge_decisions":     agg["judge_decisions"],
        "deployment_usage":    agg["deployment_summary"],
        "elapsed_ms":          elapsed,
        "anchor_positive":     domain.build_positive_descriptor(entry, summary),
        "anchor_negative":     params.NEGATIVE_DESCRIPTOR,
        "judge_concurrency":   params.JUDGE_CONCURRENCY,
        "judge_router":        "deep-agent",
    }
    domains.dd.pipeline.observability.service.span_attrs(params.STEP, {
        "kept": stats["kept"], "dropped": stats["dropped"], "llm_judged": n_judged, "llm_deduped": n_deduped,
        "llm_errors": stats["llm_errors"], "domain_coherence": 0.0, "elapsed_ms": elapsed,
    })
    logger.info(
        f"[off_topic] {slug}: kept {stats['kept']}/{n} (dropped {stats['dropped']}); judged={n_judged} "
        f"(deduped {n_deduped}, earlier runs {len(cached)}, auto-kept reference pages {len(auto_keep)}); "
        f"second look at {len(rounds[2])} drops of >={params.RECHECK_MIN_CHARS} chars: {rescued} kept; "
        f"keep={llm_kept} drop={agg['llm_dropped']}, errors={stats['llm_errors']} = {dict(sorted(agg['error_breakdown'].items()))}; {elapsed}ms"
    )
    graphs.service.emit(
        params.STEP, "done",
        kept = len(relevant), dropped = n - len(relevant), total = n,
        llm_judged = n_judged, llm_deduped = n_deduped, llm_keep = llm_kept,
        llm_drop = agg["llm_dropped"], llm_err = stats["llm_errors"],
        llm_error_breakdown = agg["error_breakdown"], wall_ms = elapsed,
    )
    return {"relevant_files": relevant, "off_topic_stats": stats}
