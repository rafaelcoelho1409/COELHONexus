"""ycs/neo4j_task — async I/O orchestration: full-transcript entity
extraction → Neo4j (Imperative Shell). Thin Celery bridge lives in
`task.py`; pure helpers (if any emerge) belong in `domain.py` (created
on first need — no trigger holds yet)."""
from __future__ import annotations
import infra
from . import params

import asyncio
import logging
import os
import random
from collections.abc import Callable
from typing import Any

import domains
from elasticsearch import AsyncElasticsearch
from langchain_neo4j import Neo4jGraph


logger = logging.getLogger(__name__)


async def ingest_async(
    video_ids:       list[str] | None,
    batch_size:      int,
    skip_resolution: bool,
    extract_id:      str | None,
    *,
    task_id:         str,
    progress_cb:     Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    with infra.langfuse.sessions.session(
        "ycs-ingest-neo4j",
        session_id = task_id or "(no-request-id)",
    ):
        with infra.otel.service.get_tracer().start_as_current_span(
            "ycs.ingest.neo4j.run",
            attributes = {
                "coelho.langfuse.keep": True,
                "coelho.langfuse.kind": "workflow_root",
                "langfuse.trace.name": "ycs.ingest.neo4j.run",
                "langfuse.observation.metadata.workflow": "ycs_ingest",
                "ycs.ingest.kind": "neo4j",
                "ycs.batch_size": int(batch_size),
                "ycs.video_count": len(video_ids or []),
            },
        ):
            infra.langfuse.spans.set_current_span_langfuse_io(input_data = {
                "kind": "neo4j",
                "video_ids_preview": list(video_ids or [])[:10],
                "video_count": len(video_ids or []),
                "batch_size": batch_size,
                "task_id": task_id or "",
            })
            infra.langfuse.spans.set_current_span_langfuse_trace_metadata({
                "pipeline": "ycs_ingest",
                "kind": "neo4j",
                "task_id": task_id or "",
                "video_count": len(video_ids or []),
                "batch_size": batch_size,
            })
            infra.langfuse.spans.set_current_span_langfuse_observation_metadata({
                "kind": "neo4j",
                "video_count": len(video_ids or []),
            })
            try:
                result = await ingest_pipeline(
            video_ids, batch_size, skip_resolution, extract_id,
            progress_cb = progress_cb,
        )
            except Exception as e:
                infra.langfuse.spans.set_current_span_langfuse_io(output_data = {
                    "status": "failed",
                    "kind": "neo4j",
                    "task_id": task_id or "",
                    "error": f"{type(e).__name__}: {e}",
                })
                raise
            infra.langfuse.spans.set_current_span_langfuse_io(output_data = {
                "status": "done" if not result.get("error") else "failed",
                "kind": "neo4j",
                "task_id": task_id or "",
                "result": result,
            })
            return result


async def ingest_pipeline(
    video_ids:       list[str] | None,
    batch_size:      int,
    skip_resolution: bool,
    extract_id:      str | None,
    *,
    progress_cb:     Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    es = AsyncElasticsearch(
        hosts      = [os.environ["ELASTICSEARCH_HOST"]],
        basic_auth = (
            os.environ["ELASTICSEARCH_USERNAME"],
            os.environ.get("ELASTICSEARCH_PASSWORD", ""),
        ),
        verify_certs = False,
    )
    # Deprecated did NOT pass refresh_schema=False here — port-fidelity.
    neo4j_graph = Neo4jGraph(
        url      = os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        username = os.environ.get("NEO4J_USERNAME", "neo4j"),
        password = os.environ.get("NEO4J_PASSWORD", ""),
    )
    # 2026-09-14: initialized up here (not where the client is
    # created below) so the `finally` can always reference them,
    # including the early `return {"error": ...}` path.
    _preview_redis = None
    _preview_tasks: set = set()
    # 2026-09-13: dropped the old "at least one of these 6 per-provider
    # keys must be set" gate — it checked credentials from the bundled-
    # litellm-router era that no longer determine chat readiness. Chat
    # (entity extraction) always goes through the LLM Endpoint
    # (`domains.settings.chat.service.build_chat_model`), which resolves to
    # a working default even with zero explicit Settings-page config —
    # there's no meaningful "unconfigured" state left to gate on. A
    # genuinely unreachable endpoint now surfaces as a real error from
    # the actual call, same as everywhere else this session moved away
    # from synthetic pre-flight checks (see the DD readiness-gate
    # finding: a hardcoded-True check is worse than no check).
    try:
        progress_cb({"phase": "fetching"})
        transcripts = await domains.ycs.ingestion.service.fetch_transcripts_from_es(es, video_ids)
        all_video_ids = list({t["video_id"] for t in transcripts})
        # 2026-09-14: with chunked streaming dispatch (a chunk can
        # now carry several video_ids, not just one), a transcript
        # missing from ES for SOME of them is a real, expected case
        # — not just the "all missing" case the old single-video
        # check covered. Every requested id that ISN'T in
        # `all_video_ids` still has to be marked done (as a
        # failure) in streaming mode, or the exactly-once finalize
        # counter never reaches its total and the run hangs waiting
        # on a video that will never report in.
        missing_ids = [
            vid for vid in (video_ids or []) if vid not in all_video_ids
        ]
        if missing_ids and skip_resolution and extract_id:
            r = domains.ycs.pipeline_task.service.build_redis_client()
            try:
                for vid in missing_ids:
                    # part_total unknowable here (the ES doc this
                    # id would have carried it on doesn't exist) —
                    # a partition genuinely missing from ES this
                    # early is a rare edge case; parent_video_id is
                    # still derived from the id string so it at
                    # least reports under the right group instead
                    # of double-counting a split video's total.
                    await domains.ycs.pipeline_task.service.mark_video_or_partition_done(
                        r, extract_id, "neo4j", vid, success = False,
                        extra = {"error": "no transcript found in ES"},
                        parent_video_id = domains.ycs.ingestion.domain.parent_video_id(vid),
                    )
                    await domains.ycs.pipeline_task.service.maybe_finalize(r, extract_id)
            finally:
                await r.close()
        if not transcripts:
            return {"error": "No transcripts found in ES"}
        total_videos = len(all_video_ids)
        metadata_map = await domains.ycs.ingestion.service.fetch_metadata_from_es(es, all_video_ids)
        progress_cb({
            "phase": "metadata_graph",
            "total": total_videos,
        })
        # 2026-09-15: dedupe to PARENT video ids before building the
        # Video/Channel metadata graph — `all_video_ids` carries one
        # entry PER PARTITION for a split video (correct for
        # Document nodes, one per piece), but `domains.ycs.graph_builder.service.build_video_metadata_graph`
        # MERGEs on `video_id` verbatim, so passing partition ids
        # through unchanged created one duplicate `Video` node per
        # partition (`id: "xyz#p1"`, `"xyz#p2"`, …) instead of one
        # canonical node for the parent — confirmed live on a
        # 4-partition video. `metadata_map` already holds the
        # correct parent-level metadata under EVERY partition key
        # (`fetch_metadata_from_es` resolves partitions to their
        # parent before querying ES) — just keep the first
        # occurrence per parent and re-tag it with the parent id.
        _seen_parents: set[str] = set()
        video_metadata = []
        for vid in all_video_ids:
            parent = domains.ycs.ingestion.domain.parent_video_id(vid)
            if parent in _seen_parents:
                continue
            _seen_parents.add(parent)
            video_metadata.append({**metadata_map.get(vid, {}), "video_id": parent})
        domains.ycs.graph_builder.service.build_video_metadata_graph(neo4j_graph, video_metadata)
        # Cumulative progress adapter: segment-local callback restarts at 0/1; this keeps the bar advancing.
        completed_global: set[str] = set()
        try:
            rows = neo4j_graph.query(
                f"MATCH (d:Document:{domains.ycs.graph_builder.params.SOURCE_LABEL}) "
                "WHERE d.video_id IN $video_ids "
                "RETURN collect(DISTINCT d.video_id) AS processed_ids",
                params = {"video_ids": all_video_ids},
            )
            if rows and rows[0].get("processed_ids"):
                completed_global = {
                    str(vid) for vid in rows[0]["processed_ids"] if vid
                }
        except Exception:
            completed_global = set()

        def _ordered(ids: set[str]) -> list[str]:
            return [vid for vid in all_video_ids if vid in ids]

        # 2026-09-14: display-only in-chunk preview (see
        # `pipeline_task.service.update_phase_preview`) — the bar
        # polls the per-video aggregator, which only advances when
        # a whole CHUNK task reports. Without this push the bar
        # sits at 0/N while videos visibly succeed in the logs.
        # Fire-and-forget tasks, awaited in the `finally` below so
        # none is destroyed mid-write; failures swallow (display
        # must never break extraction). (`_preview_redis` /
        # `_preview_tasks` are initialized at the top of
        # `_run_inner` so the `finally` is safe on every path.)
        if skip_resolution and extract_id:
            try:
                _preview_redis = domains.ycs.pipeline_task.service.build_redis_client()
            except Exception:
                _preview_redis = None

        def _push_preview() -> None:
            if _preview_redis is None or not extract_id:
                return
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            try:
                t = loop.create_task(
                    domains.ycs.pipeline_task.service.update_phase_preview(
                        _preview_redis, extract_id, "neo4j",
                        _ordered(completed_global),
                    )
                )
                _preview_tasks.add(t)
                t.add_done_callback(_preview_tasks.discard)
            except Exception:
                pass

        def _neo4j_progress(payload: dict[str, Any]) -> None:
            phase = payload.get("phase")
            if phase == "extracting":
                seg_completed = {
                    str(vid)
                    for vid in (payload.get("completed_ids") or [])
                    if vid
                }
                seg_failed = {
                    str(vid)
                    for vid in (payload.get("failed_ids") or [])
                    if vid
                }
                completed_global.update(seg_completed)
                active_failed = {
                    vid for vid in seg_failed if vid not in completed_global
                }
                _push_preview()
                current = min(
                    total_videos,
                    len(completed_global) + len(active_failed),
                )
                meta = dict(payload)
                meta["current"] = current
                meta["total"] = total_videos
                meta["current_batch"] = current
                meta["total_batches"] = total_videos
                meta["completed_ids"] = _ordered(completed_global)
                meta["failed_ids"] = _ordered(active_failed)
                progress_cb(meta)
                return
            if phase == "resolving":
                meta = dict(payload)
                meta["current"] = len(completed_global)
                meta["total"] = total_videos
                meta["completed_ids"] = _ordered(completed_global)
                meta["failed_ids"] = []
                progress_cb(meta)
                return
            progress_cb(payload)
        llm = domains.settings.chat.service.build_chat_model(timeout_s = 650.0)
        logger.info(
            f"[ingest_to_neo4j] videos={len(all_video_ids)}, "
            f"max_retry_passes={params.MAX_RETRY_PASSES}",
        )
        agg_nodes = 0
        agg_rels = 0
        agg_attempted = 0
        agg_merged = 0
        agg_error_breakdown: dict[str, int] = {}
        final_failed_ids: list[str] = []
        consecutive_infra_passes = 0
        pending_transcripts = transcripts
        extraction_stats: dict[str, Any] = {}
        for attempt in range(params.MAX_RETRY_PASSES + 1):
            # 2026-09-14: cooperative-cancel checkpoint — only
            # between passes (never mid-pass; a pass's own
            # `domains.ycs.graph_builder.service.extract_and_store_graph` call always runs to
            # completion for whatever it's already dispatched).
            # Stop's replacement for `revoke(terminate=True)` — see
            # `pipeline_task.service.revoke_pipeline_phases`.
            if attempt > 0 and extract_id:
                try:
                    _cr = domains.ycs.pipeline_task.service.build_redis_client()
                    try:
                        if await domains.ycs.pipeline_task.service.is_pipeline_cancelled(_cr, extract_id):
                            logger.info(
                                f"[ingest_to_neo4j] {extract_id}: "
                                f"cancelled — stopping retry passes "
                                f"({len(final_failed_ids)} video(s) "
                                f"left unretried)"
                            )
                            break
                    finally:
                        await _cr.close()
                except Exception as e:
                    logger.warning(
                        f"[ingest_to_neo4j] cancel check failed: "
                        f"{type(e).__name__}: {e}"
                    )
            if attempt > 0:
                # Jittered backoff between passes (DD pattern,
                # scaled for heavy calls): immediate retries hammer
                # an already-exhausted free-tier pool.
                _lo, _hi = domains.ycs.graph_builder.params.RETRY_PASS_BACKOFF_S
                _sleep_s = _lo + random.uniform(0, _hi - _lo)
                logger.info(
                    f"[ingest_to_neo4j] backing off {_sleep_s:.1f}s "
                    f"before retry pass {attempt}/{params.MAX_RETRY_PASSES}"
                )
                await asyncio.sleep(_sleep_s)
            pass_label = (
                "First pass" if attempt == 0
                else f"Retry pass {attempt}/{params.MAX_RETRY_PASSES}"
            )
            logger.info(
                f"[ingest_to_neo4j] {pass_label}: "
                f"{len(pending_transcripts)} transcript(s)",
            )
            extraction_stats = await domains.ycs.graph_builder.service.extract_and_store_graph(
                transcripts  = pending_transcripts,
                metadata_map = metadata_map,
                llm          = llm,
                neo4j_graph  = neo4j_graph,
                batch_size   = batch_size,
                progress_cb  = _neo4j_progress,
                # Resolution is a global Neo4j pass — run it ONCE
                # after the retry loop, not per pass.
                run_resolution = False,
                extract_id     = extract_id,
            )
            agg_nodes     += int(extraction_stats.get("nodes_created", 0) or 0)
            agg_rels      += int(extraction_stats.get("relationships_created", 0) or 0)
            agg_attempted += int(extraction_stats.get("documents_processed", 0) or 0)
            for _k, _v in (extraction_stats.get("error_breakdown") or {}).items():
                agg_error_breakdown[_k] = agg_error_breakdown.get(_k, 0) + int(_v or 0)
            docs_processed = int(extraction_stats.get("documents_processed", 0) or 0)
            nodes_created  = int(extraction_stats.get("nodes_created", 0) or 0)
            if docs_processed > 0 and nodes_created == 0:
                logger.warning(
                    f"[ingest_to_neo4j] {pass_label}: {docs_processed} "
                    f"docs processed but 0 nodes created — model "
                    f"likely passed schema validation but isn't "
                    f"performing the extraction task. Last LLM error: "
                    f"{extraction_stats.get('last_batch_error')}"
                )
            failed_ids = extraction_stats.get("failed_video_ids") or []
            if not failed_ids:
                final_failed_ids = []
                break
            final_failed_ids = failed_ids
            videos_completed_this_pass = int(
                extraction_stats.get("videos_completed", 0) or 0,
            )
            if videos_completed_this_pass == 0:
                # Streak halt (DD's SUSTAINED_INFRA_OUTAGE_LIMIT,
                # adapted to passes): consecutive all-failed INFRA
                # passes stop early — the provider is down, not
                # flaky. Non-infra zeros (model/silent-zero) break
                # immediately: re-attempting won't heal those.
                # Any success resets the streak (handled below —
                # this branch only runs on zero successes).
                _last_err = str(
                    extraction_stats.get("last_batch_error") or ""
                )
                if domains.ycs.graph_builder.domain.is_infra_error(_last_err):
                    # 2026-09-17: a pass with too few pending videos
                    # can't tell "provider is down" apart from "this
                    # one item got unlucky twice" — see
                    # domains.ycs.graph_builder.params.MIN_PENDING_FOR_INFRA_HALT's comment. Below the
                    # threshold, retry normally without touching the
                    # streak counter at all.
                    if len(pending_transcripts) < domains.ycs.graph_builder.params.MIN_PENDING_FOR_INFRA_HALT:
                        logger.warning(
                            f"[ingest_to_neo4j] {pass_label} produced 0 "
                            f"successes out of {len(pending_transcripts)} "
                            f"attempted (below the "
                            f"{domains.ycs.graph_builder.params.MIN_PENDING_FOR_INFRA_HALT}-video infra-"
                            f"halt threshold — retrying without "
                            f"counting toward the streak)"
                        )
                        if attempt < params.MAX_RETRY_PASSES:
                            pending_transcripts = [
                                t for t in transcripts
                                if t["video_id"] in failed_ids
                            ]
                            continue
                    else:
                        consecutive_infra_passes += 1
                        if consecutive_infra_passes >= domains.ycs.graph_builder.params.MAX_CONSECUTIVE_INFRA_PASSES:
                            logger.error(
                                f"[ingest_to_neo4j] {pass_label} produced 0 "
                                f"successes out of {len(pending_transcripts)} "
                                f"attempted ({consecutive_infra_passes}x "
                                f"consecutive infra failure) — provider "
                                f"down, giving up on the remaining "
                                f"{len(failed_ids)} video(s) for this run: "
                                f"{failed_ids[:10]} "
                                f"(breakdown={agg_error_breakdown})"
                            )
                            break
                        logger.warning(
                            f"[ingest_to_neo4j] {pass_label} produced 0 "
                            f"successes (infra streak "
                            f"{consecutive_infra_passes}/"
                            f"{domains.ycs.graph_builder.params.MAX_CONSECUTIVE_INFRA_PASSES}) — one more "
                            f"pass after backoff"
                        )
                        if attempt < params.MAX_RETRY_PASSES:
                            pending_transcripts = [
                                t for t in transcripts
                                if t["video_id"] in failed_ids
                            ]
                            continue
                logger.error(
                    f"[ingest_to_neo4j] {pass_label} produced 0 "
                    f"successes out of {len(pending_transcripts)} "
                    f"attempted — endpoint likely down, giving up on "
                    f"the remaining {len(failed_ids)} video(s) for "
                    f"this run: {failed_ids[:10]} "
                    f"(breakdown={agg_error_breakdown})"
                )
                break
            if attempt < params.MAX_RETRY_PASSES:
                # This pass had ≥1 success (zero-success passes break
                # above) — reset the infra streak: the provider is
                # flaky, not down.
                consecutive_infra_passes = 0
                logger.warning(
                    f"[ingest_to_neo4j] {len(failed_ids)} video(s) "
                    f"failed {pass_label} — retrying "
                    f"({attempt + 1}/{params.MAX_RETRY_PASSES}): "
                    f"{failed_ids[:10]}"
                )
                pending_transcripts = [
                    t for t in transcripts
                    if t["video_id"] in failed_ids
                ]
            else:
                logger.error(
                    f"[ingest_to_neo4j] retry budget exhausted after "
                    f"{params.MAX_RETRY_PASSES} retries — giving up with "
                    f"partial results ({len(failed_ids)} video(s) "
                    f"unprocessed): {failed_ids[:10]}"
                )
        if skip_resolution:
            # Streaming mode: this call handles a CHUNK of up to
            # `EXTRACT_CONCURRENCY` videos for `extract_id`'s Neo4j
            # phase (2026-09-14 — reverted from one-Celery-task-per-
            # video after that design starved on the shared 2-slot
            # worker pool; see `extract/task.py`'s `_on_video_indexed`
            # comment for the full story). Report EVERY video in the
            # chunk individually — `mark_video_done`'s counter is
            # per-video regardless of how many videos one task
            # handles. Whichever video's increment turns out to be
            # the LAST one for the whole run triggers resolution
            # exactly once (unconditionally safe/idempotent even if
            # THIS chunk created 0 nodes but an earlier one did).
            if extract_id and all_video_ids:
                # 2026-09-14: long-video partitioning — `part_total`
                # per video, read from the SAME `transcripts` this
                # pass already fetched (now carrying it thanks to
                # `_scroll_transcripts`'s widened `_source`).
                part_total_by_vid = {
                    t["video_id"]: t.get("part_total")
                    for t in transcripts if isinstance(t, dict)
                }
                r = domains.ycs.pipeline_task.service.build_redis_client()
                try:
                    for i, vid in enumerate(all_video_ids):
                        # Chunk-level aggregates (agg_nodes/agg_rels)
                        # describe the WHOLE chunk, not this one
                        # video — attach them to only the FIRST
                        # video in the chunk so `get_phase_progress`'s
                        # cross-video summation counts them once,
                        # not once per video in the chunk. NOTE: if
                        # that first video is itself one partition
                        # of a split video sharing a chunk with
                        # OTHER unrelated videos, this chunk-wide
                        # total isn't attributable purely to that
                        # partition — a known, cosmetic imprecision
                        # in the displayed node/rel count for that
                        # case, not a correctness issue (the actual
                        # graph data is unaffected either way).
                        extra = (
                            {
                                "nodes_created":         agg_nodes,
                                "relationships_created": agg_rels,
                            } if i == 0 else {}
                        )
                        parent_vid = domains.ycs.ingestion.domain.parent_video_id(vid)
                        finished, total = await domains.ycs.pipeline_task.service.mark_video_or_partition_done(
                            r, extract_id, "neo4j", vid,
                            success = vid not in final_failed_ids,
                            extra = extra,
                            parent_video_id = parent_vid,
                            part_total = part_total_by_vid.get(vid),
                        )
                        if total is not None and finished is not None and finished >= total:
                            logger.info(
                                f"[ingest_to_neo4j] {extract_id}: last "
                                f"video of the run's Neo4j phase "
                                f"({finished}/{total}) — running entity "
                                f"resolution once"
                            )
                            # 2026-09-15: `finished >= total` (just
                            # above) already flips the poller-facing
                            # state to SUCCESS/100% — but
                            # `entities_merged` isn't known until
                            # this whole-graph resolve pass finishes
                            # (tens of seconds on a large graph).
                            # `neo4j_resolving_key` tells
                            # `get_phase_progress` to keep reporting
                            # PROGRESS until it's actually written,
                            # so the bar doesn't freeze at "0
                            # merged". TTL is a backstop if this
                            # process dies mid-resolution.
                            await r.set(
                                domains.ycs.pipeline_task.keys.neo4j_resolving_key(extract_id), "1", ex = 300,
                            )
                            try:
                                agg_merged = await domains.ycs.graph_builder.service.resolve_entities(neo4j_graph)
                                # entities_merged is a WHOLE-RUN number,
                                # only known now — patched onto the
                                # DISPLAYED status entry: parent_vid, not
                                # vid — a partition's own id never gets
                                # its own phase_status_key entry (its
                                # outcome lives in the partition-group
                                # hash until the group completes and
                                # folds into one entry under the parent).
                                await domains.ycs.pipeline_task.service.update_video_extra(
                                    r, extract_id, "neo4j", parent_vid,
                                    {"entities_merged": agg_merged},
                                )
                            finally:
                                await r.delete(domains.ycs.pipeline_task.keys.neo4j_resolving_key(extract_id))
                        await domains.ycs.pipeline_task.service.maybe_finalize(r, extract_id)
                finally:
                    await r.close()
        elif agg_nodes > 0:
            # Entity resolution — ONCE after all segments (previously ran per-segment, 4× redundant).
            progress_cb({
                "phase": "resolving",
                "nodes": agg_nodes,
                "rels":  agg_rels,
            })
            logger.info("[ingest_to_neo4j] entity resolution starting")
            agg_merged = await domains.ycs.graph_builder.service.resolve_entities(neo4j_graph)
            logger.info(
                f"[ingest_to_neo4j] entity resolution: "
                f"{agg_merged} nodes merged"
            )
        # 2026-09-13: build the result from `completed_global` (the
        # same cumulative set the live progress bar uses — updated by
        # `_neo4j_progress` after every pass, and seeded from Neo4j's
        # actual tagged Documents so it also counts videos already
        # done from a prior interrupted run), NOT `**extraction_stats`
        # — that dict is only the LAST pass's stats. Spreading it here
        # previously made the task's own self-reported
        # videos_completed/videos_failed badly understate a multi-
        # pass run's real outcome (a run with 11 real successes
        # across 4 passes once reported `videos_completed: 2` — only
        # the last pass's count).
        final_completed_ids = _ordered(completed_global)
        return {
            "videos_processed":      len(all_video_ids),
            "documents_processed":   agg_attempted,
            "nodes_created":         agg_nodes,
            "relationships_created": agg_rels,
            "entities_merged":       agg_merged,
            "videos_completed":      len(final_completed_ids),
            "completed_video_ids":   final_completed_ids,
            "videos_failed":         len(final_failed_ids),
            "failed_video_ids":      final_failed_ids,
            "last_batch_error":      extraction_stats.get("last_batch_error"),
            "error_breakdown":       agg_error_breakdown,
        }
    finally:
        if _preview_tasks:
            await asyncio.gather(*_preview_tasks, return_exceptions = True)
        if _preview_redis is not None:
            try:
                await _preview_redis.close()
            except Exception:
                pass
        await es.close()
