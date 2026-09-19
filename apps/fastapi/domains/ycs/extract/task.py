"""ycs/extract — Celery tasks: yt-dlp metadata + Playwright transcripts → ES.
Three tasks
(one per ingestion mode: by video IDs, by channel, by playlist). Each:
  1. opens a fresh `AsyncElasticsearch` for the worker process
  2. dispatches `YtDlpExtractor.extract_{batch,channel,playlist}`
  3. bulk-indexes metadata via `domains.ycs.es_index`
  4. (if `include_transcription`) initializes Playwright service,
     runs `domains.ycs.transcript.service.fetch_transcriptions_batch`, and bulk-indexes transcripts
  5. always closes ES (and the transcript service when used)

Celery is sync; async work is wrapped in `asyncio.run(...)`. The
`@app.task(bind=True)` decorator gives access to `self.update_state(...)`
for progress reporting, which Flower and `GET /tasks/{id}` consume.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

import domains
from celery.utils.log import get_task_logger
from elasticsearch import AsyncElasticsearch

from infra.celery import app

from . import service


# Callback signature for live progress emission. The task wrapper
# supplies a closure that pipes payloads into `self.update_state(meta=)`;
# the async impl passes per-stage dicts so the FastHTML poller can show
# the current video metadata + phase counters.
ProgressCb = Callable[[dict[str, Any]], None]


def _project_video_meta(v: dict[str, Any]) -> dict[str, Any]:
    """Pluck the subset of yt-dlp metadata shown on the FastHTML
    progress card — same shape as the Search-page result row (minus
    the thumbnail). Centralized so the 3 extract paths emit identical
    payloads."""
    return {
        "id":              v.get("id"),
        "title":           v.get("title"),
        "channel":         v.get("channel"),
        "channel_id":      v.get("channel_id"),
        "duration":        v.get("duration"),
        "duration_string": v.get("duration_string"),
        "view_count":      v.get("view_count"),
        "like_count":      v.get("like_count"),
        "upload_date":     v.get("upload_date"),
        "webpage_url":     v.get("webpage_url"),
    }


logger = get_task_logger(__name__)


# Fresh client factory (Celery worker process)
def _get_es_client() -> AsyncElasticsearch:
    """Build a fresh ES client owned by the running Celery task. The infra
    `get_es()` singleton lives in the FastAPI process; the Celery worker
    is a separate process and needs its own connection pool — deprecated
    pattern (`tasks/youtube/crawler.py:L27-38`)."""
    return AsyncElasticsearch(
        hosts      = [os.environ["ELASTICSEARCH_HOST"]],
        basic_auth = (
            os.environ["ELASTICSEARCH_USERNAME"],
            os.environ.get("ELASTICSEARCH_PASSWORD", ""),
        ),
        verify_certs = False,
    )


async def _dispatch_streaming_totals(
    extract_id: str,
    video_ids: list[str],
    dispatched_ids: list[str],
    include_transcription: bool,
) -> None:
    """Called once, after `_extract_videos_async`'s fetch loop finishes
    dispatching every video's downstream work. Records the Neo4j/Qdrant
    phase totals so `pipeline_task.service.maybe_finalize` knows when
    the run is actually done, then makes one speculative finalize check
    itself — covering the (unlikely but real) race where every
    dispatched per-video task already finished before this function got
    a chance to correct the totals.

    2026-09-14: totals are corrected down to `len(dispatched_ids)` (K —
    videos that actually got a real per-video/chunk task; excludes
    no-transcript/fetch-failed/index-write-failed videos entirely) —
    was `len(video_ids)` (N, the full requested count), with every
    excluded video synthetically marked "failed" in both phases just
    to make the counters reconcile. That synthetic marking is GONE: a
    video that never had a transcript never reaches Qdrant or Neo4j,
    so counting it as a failure there was misleading (a red/yellow
    pill for a step it was never eligible to run) — it already shows
    correctly as failed/no-transcript in Playwright/ES, where the
    determination actually happened. Every video now counted in K WILL
    naturally report in via a real task, so no workaround is needed to
    make the counters reconcile — `total` is simply seeded to N upfront
    (see `_extract_videos_async`, for early progress feedback while
    Playwright is still running) and corrected down to K here.

    A degenerate empty `dispatched_ids` (every requested video failed
    or had no transcript) falls out naturally: totals correct to 0,
    which `get_phase_progress`/`maybe_finalize` already treat as
    trivially done — no separate branch needed."""
    if not include_transcription:
        # Metadata-only run — no downstream work exists. Zero the
        # seeded totals so both stream bars read trivially done
        # instead of dangling at 0/N.
        redis = domains.ycs.pipeline_task.service.build_redis_client()
        try:
            await domains.ycs.pipeline_task.service.set_phase_total(redis, extract_id, "neo4j", 0)
            await domains.ycs.pipeline_task.service.set_phase_total(redis, extract_id, "qdrant", 0)
            await domains.ycs.pipeline_task.service.maybe_finalize(redis, extract_id)
        finally:
            await redis.close()
        return
    # 2026-09-14: long-video partitioning — `dispatched_ids` holds one
    # entry PER PARTITION (`_on_video_indexed` fires once per
    # partition, not once per original video), but the phase total
    # must count VIDEOS: a video split into 5 partitions should count
    # as 1 toward the total, exactly like an unsplit video, not 5.
    # `mark_video_or_partition_done` (pipeline_task/service.py)
    # aggregates all of a video's partition completions into exactly
    # one `mark_video_done` call for the parent id — this dedupe here
    # is what that single call is measured against.
    dispatched_count = len({
        domains.ycs.ingestion.domain.parent_video_id(vid) for vid in (dispatched_ids or [])
    })
    redis = domains.ycs.pipeline_task.service.build_redis_client()
    try:
        # 2026-09-15: PIECE-level total (partitions counted
        # individually) for the Neo4j bar's "K/M pieces" display —
        # `dispatched_ids` itself, undeduped, is exactly that count.
        # Neo4j-only per scope (Qdrant's bar is unchanged); see
        # `pipeline_task.keys.phase_piece_total_key`.
        await domains.ycs.pipeline_task.service.set_phase_piece_total(
            redis, extract_id, "neo4j", len(dispatched_ids or []),
        )
        neo4j_finished, neo4j_total = await domains.ycs.pipeline_task.service.set_phase_total(
            redis, extract_id, "neo4j", dispatched_count,
        )
        qdrant_finished, qdrant_total = await domains.ycs.pipeline_task.service.set_phase_total(
            redis, extract_id, "qdrant", dispatched_count,
        )
        # 2026-09-14: either correction — not a skip-mark any more —
        # can itself be the moment its phase completes: every real
        # video already reported in (finished == the OLD, higher seed)
        # before this call corrected total down to K. Confirmed live
        # this exact race is real for Qdrant (see this function's
        # docstring for the original incident); Neo4j gets the same
        # treatment on the same reasoning even though it's far less
        # likely to actually fire in practice (Neo4j chunk tasks take
        # minutes each — this function normally runs long before any
        # of them could have finished) — correctness here shouldn't
        # depend on that timing being reliably slow.
        qdrant_completed_here = (
            qdrant_total > 0 and qdrant_finished >= qdrant_total
        )
        neo4j_completed_here = (
            neo4j_total > 0 and neo4j_finished >= neo4j_total
        )
        if qdrant_completed_here:
            from qdrant_client import AsyncQdrantClient
            qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
            qdrant_port = int(os.environ.get("QDRANT_PORT", "6333"))
            qdrant_api_key = os.environ.get("QDRANT_API_KEY")
            qdrant = AsyncQdrantClient(
                url     = qdrant_url,
                port    = qdrant_port,
                api_key = qdrant_api_key if qdrant_api_key else None,
            )
            try:
                # `finalize_qdrant_buffer` re-queues and RAISES on a
                # transient embed/upsert failure (by design — see its
                # docstring). This call has no `mark_video_done`
                # counterpart to protect here (unlike
                # `stream_video_to_qdrant`'s), but an uncaught raise
                # would still propagate out of `_extract_videos_async`
                # and fail the WHOLE otherwise-successful extract_videos
                # task over a drain hiccup. Swallow — the chunks stay
                # re-queued in the buffer for a future Rerun's drain to
                # pick up; this run has already reported its true
                # outcome to Redis regardless.
                drained = await domains.ycs.ingestion.service.finalize_qdrant_buffer(redis, qdrant, extract_id)
                logger.info(
                    f"[extract_videos] {extract_id}: totals-correction "
                    f"completed the Qdrant phase — drained {drained} "
                    f"remaining point(s)"
                )
                # 2026-09-14: confirmed live — the drain genuinely
                # succeeded (28/28 points really landed in Qdrant) but
                # the bar still showed "0 points", because NO per-video
                # status entry ever recorded them: every one of the K
                # real videos reported `points_upserted: 0` (none of
                # them individually crossed FLUSH_CHUNKS or triggered
                # the drain themselves — the TOTAL correction did), and
                # `get_phase_progress`'s success hint only sums what's
                # in the status hash. Same pattern `qdrant_task/task.py`
                # already uses for its own drain trigger — attach the
                # count to one of the dispatched videos so the sum
                # picks it up.
                if drained and dispatched_ids:
                    await domains.ycs.pipeline_task.service.update_video_extra(
                        redis, extract_id, "qdrant", dispatched_ids[-1],
                        {"points_upserted": drained},
                    )
            except Exception as e:
                logger.warning(
                    f"[extract_videos] {extract_id}: totals-correction "
                    f"Qdrant drain failed (chunks re-queued for a "
                    f"later attempt): {type(e).__name__}: {e}"
                )
            finally:
                await qdrant.close()
        if neo4j_completed_here:
            # 2026-09-14: the WHOLE block, including building the
            # connection, is inside try/except — caught the hard way
            # while testing the Qdrant fix above: `Neo4jGraph(...)`
            # itself can raise (`ServiceUnavailable` etc.) if Neo4j is
            # transiently unreachable at exactly this moment, and an
            # uncaught raise here would crash the WHOLE otherwise-
            # successful extract_videos task over a resolution hiccup
            # — same class of risk the Qdrant drain above already
            # guards against, just missed here on the first pass.
            try:
                from langchain_neo4j import Neo4jGraph
                neo4j_graph = Neo4jGraph(
                    url      = os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
                    username = os.environ.get("NEO4J_USERNAME", "neo4j"),
                    password = os.environ.get("NEO4J_PASSWORD", ""),
                )
                merged = await domains.ycs.graph_builder.service.resolve_entities(neo4j_graph)
                logger.info(
                    f"[extract_videos] {extract_id}: totals-correction "
                    f"completed the Neo4j phase — resolution merged "
                    f"{merged} node(s)"
                )
                if merged and dispatched_ids:
                    await domains.ycs.pipeline_task.service.update_video_extra(
                        redis, extract_id, "neo4j", dispatched_ids[-1],
                        {"entities_merged": merged},
                    )
            except Exception as e:
                logger.warning(
                    f"[extract_videos] {extract_id}: totals-correction "
                    f"entity resolution failed: {type(e).__name__}: {e}"
                )
        await domains.ycs.pipeline_task.service.maybe_finalize(redis, extract_id)
    finally:
        await redis.close()


# Async implementations (called via asyncio.run from the Celery tasks)
async def _extract_videos_async(
    video_ids:             list[str],
    include_transcription: bool,
    languages:             list[str] | None,
    progress_cb:           ProgressCb | None = None,
    extract_id:            str | None        = None,
) -> dict[str, Any]:
    es = _get_es_client()
    extractor = service.get_extractor()
    try:
        if progress_cb:
            progress_cb({
                "phase":   "metadata",
                "current": 0,
                "total":   len(video_ids),
            })
        if extract_id and include_transcription and video_ids:
            # 2026-09-14: seed the streaming phase totals UPFRONT so
            # the Qdrant/Neo4j bars track per-video completions IN
            # PARALLEL with Playwright (instead of sitting at
            # PENDING/0% until this whole function finishes and only
            # then jumping). Seeded to `len(video_ids)` (N, the full
            # requested count — the only number known this early);
            # `_dispatch_streaming_totals` corrects it DOWN to the real
            # dispatched count (K, excluding no-transcript/failed
            # videos) once that's known, at the end.
            try:
                _r = domains.ycs.pipeline_task.service.build_redis_client()
                try:
                    await domains.ycs.pipeline_task.service.set_phase_total(_r, extract_id, "neo4j", len(video_ids))
                    await domains.ycs.pipeline_task.service.set_phase_total(_r, extract_id, "qdrant", len(video_ids))
                finally:
                    await _r.close()
            except Exception as _seed_err:
                logger.warning(
                    f"[extract_videos] streaming totals seed failed: "
                    f"{type(_seed_err).__name__}: {_seed_err}"
                )
        videos = await extractor.extract_batch(video_ids)
        videos_dicts = [
            v.model_dump(exclude_none = False) if hasattr(v, "model_dump") else v
            for v in videos
        ]
        es_metadata = await domains.ycs.es_index.service.index_videos_to_elasticsearch(es, videos_dicts)
        # transcript progress callback uses it to surface the most
        # recently completed video to the UI.
        videos_meta_map: dict[str, dict[str, Any]] = {
            v["id"]: _project_video_meta(v)
            for v in videos_dicts if v.get("id")
        }
        # column video list can render with titles + channels
        # immediately — before any transcript fetch finishes. Otherwise
        # the user would stare at video_ids until Phase 1 completes,
        # then suddenly see full metadata. We also stash `all_items`
        # in a closure variable + replay it on every subsequent
        # transcription progress payload (Celery `update_state(meta=)`
        # REPLACES the dict each call — a one-shot emit would be
        # overwritten before the JS poll lands on it).
        all_items = [
            videos_meta_map[vid] for vid in video_ids
            if vid in videos_meta_map
        ]
        if progress_cb:
            progress_cb({
                "phase":     "metadata_done",
                "current":   0,
                "total":     len(video_ids),
                "all_items": all_items,
            })
        es_transcriptions = {"indexed": 0, "failed": 0}
        # 2026-09-14: declared HERE (not inside `if include_transcription:`
        # below) — both are referenced after that block, in the final
        # return dict and in `_dispatch_streaming_totals`'s call, which
        # run regardless of `include_transcription`. Python treats a
        # name assigned anywhere in a function as local to the whole
        # function; leaving these to be defined only inside the `if`
        # raised `UnboundLocalError` on a metadata-only
        # (`include_transcription=False`) run.
        dispatched_ids:     list[str] = []
        no_transcript_ids:  list[str] = []
        if include_transcription:
            valid_ids = [
                v["id"] for v in videos_dicts
                if v.get("id") and "error" not in v
            ]
            video_metadata = {
                v["id"]: {
                    "channel_id":  v.get("channel_id"),
                    "playlist_id": v.get("playlist_id"),
                }
                for v in videos_dicts if v.get("id")
            }

            # 2026-09-14: Neo4j no longer gets one Celery task PER VIDEO.
            # Live-tested on a 25-video Capital Global batch: with only
            # 2 total Celery worker slots (shared across every domain)
            # and `extract_videos` itself pinning one slot for its whole
            # run, per-video dispatch left exactly 1 slot for ALL
            # downstream work — 21 videos ran through Neo4j strictly
            # one at a time, throwing away `extract_and_store_graph`'s
            # own internal `domains.ycs.graph_builder.params.EXTRACT_CONCURRENCY`-wide (5) asyncio pool
            # entirely (a pool of width 5 processing 1 document at a
            # time buys nothing). That's a real regression from the
            # PRE-streaming design, where one `ingest_to_neo4j` call
            # processed 5 videos concurrently.
            #
            # Fix: accumulate video ids into a chunk here (in-process —
            # `extract_videos` is the one place already watching every
            # video complete, in order, so no Redis queue is needed for
            # this) and flush ONE `ingest_to_neo4j` call per chunk, with
            # `batch_size=len(chunk)` so the internal pool width exactly
            # matches the chunk — full concurrency restored, while still
            # starting well before the whole 25-video batch finishes
            # Phase 1 (chunks flush every `domains.ycs.graph_builder.params.EXTRACT_CONCURRENCY` videos,
            # not after all 25). `ingest_to_neo4j` already natively
            # accepts and pools a list — this reverts ONLY the call
            # site back to batching, not the task's own logic.
            #
            # Qdrant stays per-video: its tasks complete in ~0.1-0.5s
            # each (chunk + buffer-push, occasionally a flush) — never
            # the bottleneck, no reason to add chunking complexity there.
            neo4j_chunk: list[str] = []
            _pending_track_tasks: list[asyncio.Task] = []

            def _flush_neo4j_chunk() -> None:
                nonlocal neo4j_chunk
                if not neo4j_chunk:
                    return
                from domains.ycs.neo4j_task.task import ingest_to_neo4j
                chunk = neo4j_chunk
                neo4j_chunk = []
                neo4j_task = ingest_to_neo4j.si(
                    chunk, len(chunk), skip_resolution = True, extract_id = extract_id,
                ).apply_async()
                _track_dispatched(neo4j_task.id)

            def _track_dispatched(task_id: str) -> None:
                # Gathered before this function returns (see the
                # `asyncio.gather` below) instead of pure fire-and-
                # forget — the previous fire-and-forget version showed
                # up live as repeated "Task was destroyed but it is
                # pending" warnings (the event loop tore down before
                # some of these ever got to run), meaning Stop's
                # revoke list was silently incomplete.
                async def _track() -> None:
                    r = domains.ycs.pipeline_task.service.build_redis_client()
                    try:
                        await domains.ycs.pipeline_task.service.track_dispatched_task(r, extract_id, task_id)
                    finally:
                        await r.close()
                _pending_track_tasks.append(asyncio.ensure_future(_track()))

            def _on_video_indexed(vid: str) -> None:
                # 2026-09-13: fires the instant a video's transcript is
                # safely committed to ES (freshly fetched, OR already
                # cached from a prior run) — dispatches that video's
                # Neo4j + Qdrant streaming work immediately, instead of
                # waiting for the whole batch to clear Phase 1 first.
                # `extract_id` is this task's own id (`self.request.id`,
                # threaded in from `extract_videos`) — the namespace
                # every downstream piece of Redis bookkeeping shares.
                from domains.ycs.qdrant_task.task import stream_video_to_qdrant
                dispatched_ids.append(vid)
                neo4j_chunk.append(vid)
                if len(neo4j_chunk) >= domains.ycs.graph_builder.params.EXTRACT_CONCURRENCY:
                    _flush_neo4j_chunk()
                qdrant_task = stream_video_to_qdrant.si(
                    vid, extract_id,
                ).apply_async()
                _track_dispatched(qdrant_task.id)
            # Per-video status tracking for the Ingest-page video list.
            # The right-column status pill is derived from these lists:
            #   id in failed_ids                → Failed
            #   id == current_item.id (active)  → Processing
            #   id in completed_ids             → Done
            #   else                            → Queued
            completed_ids: list[str] = []
            failed_ids:    list[str] = []
            # `no_transcript_ids` tracked separately from `failed_ids`
            # (declared at the top of this function, alongside
            # `dispatched_ids`) — a no-transcript video is never a
            # candidate for ES/Qdrant/Neo4j processing at all (not just
            # "failed" at it), so the drawer table renders it with a
            # distinct "N/A" pill for those 3 columns instead of a
            # misleading red "Failed" one. Playwright's own column is
            # unaffected (still shows via `failed_ids` there — that
            # step IS what made the determination).
            if progress_cb:
                progress_cb({
                    "phase":             "transcription",
                    "current":           0,
                    "total":             len(valid_ids),
                    "completed_ids":     list(completed_ids),
                    "failed_ids":        list(failed_ids),
                    "no_transcript_ids": list(no_transcript_ids),
                    "all_items":         all_items,
                })

            def _per_video_cb(
                done: int, total: int, video_id: str | None,
                success: bool = True, *, no_transcript: bool = False,
            ) -> None:
                if not progress_cb:
                    return
                if video_id:
                    if success and video_id not in completed_ids:
                        completed_ids.append(video_id)
                    elif (not success) and video_id not in failed_ids:
                        failed_ids.append(video_id)
                    if no_transcript and video_id not in no_transcript_ids:
                        no_transcript_ids.append(video_id)
                payload: dict[str, Any] = {
                    "phase":             "transcription",
                    "current":           done,
                    "total":             total,
                    "completed_ids":     list(completed_ids),
                    "failed_ids":        list(failed_ids),
                    "no_transcript_ids": list(no_transcript_ids),
                    "all_items":         all_items,
                }
                if video_id and video_id in videos_meta_map:
                    payload["current_item"] = videos_meta_map[video_id]
                progress_cb(payload)

            transcript_service = await domains.ycs.transcript.service.init_transcript_service(
                max_concurrent           = domains.ycs.transcript.params.MAX_CONCURRENT,
                browser_refresh_interval = 10,
                max_retries              = 3,
            )
            # 2026-09-14: cooperative-cancel — checked once per Playwright
            # chunk (see `domains.ycs.transcript.service.fetch_transcriptions_batch`'s `cancel_check`
            # param). A fresh client + raw GET per chunk boundary (every
            # ~10 videos) is cheap enough to skip throttling; this is the
            # Stop button's replacement for `revoke(terminate=True)`,
            # which was observed live to wedge Celery's prefork pool
            # (see `pipeline_task.service.revoke_pipeline_phases`).
            async def _cancel_check() -> bool:
                if not extract_id:
                    return False
                r = domains.ycs.pipeline_task.service.build_redis_client()
                try:
                    return await domains.ycs.pipeline_task.service.is_pipeline_cancelled(r, extract_id)
                except Exception:
                    return False
                finally:
                    await r.close()
            def _es_index_cb(indexed: int, total: int) -> None:
                # Phase 2 (ElasticSearch) — separate bar from Phase 1
                # (Playwright). Chunk-grained by nature (one bulk write
                # per chunk), so this ticks in jumps, not smoothly.
                #
                # Carries completed_ids/failed_ids/all_items forward from
                # the transcription phase — Celery's update_state REPLACES
                # the meta dict each call, so without this the drawer
                # table's per-video ES column would go blank the instant
                # this phase starts (its own payload has no per-video
                # detail, only phase/current/total).
                if not progress_cb:
                    return
                try:
                    progress_cb({
                        "phase":             "es_indexing",
                        "current":           indexed,
                        "total":             total,
                        "completed_ids":     list(completed_ids),
                        "failed_ids":        list(failed_ids),
                        "no_transcript_ids": list(no_transcript_ids),
                        "all_items":         all_items,
                    })
                except Exception as cb_err:
                    logger.warning(
                        f"[extract_videos] es_index progress_cb raised: "
                        f"{type(cb_err).__name__}: {cb_err}"
                    )
            try:
                trans_stats: dict[str, int] = {}
                await domains.ycs.transcript.service.fetch_transcriptions_batch(
                    valid_ids,
                    transcript_service = transcript_service,
                    es_client          = es,
                    languages          = languages,
                    video_metadata     = video_metadata,
                    progress_cb        = _per_video_cb if progress_cb else None,
                    es_progress_cb     = _es_index_cb if progress_cb else None,
                    on_video_indexed   = _on_video_indexed if extract_id else None,
                    stats              = trans_stats,
                    cancel_check       = _cancel_check if extract_id else None,
                )
                # 2026-09-13: ES indexing now happens PER-VIDEO inside
                # `domains.ycs.transcript.service.fetch_transcriptions_batch` itself (each doc is
                # written the instant its video's fetch succeeds, so
                # `_on_video_indexed` can safely trigger downstream
                # Neo4j/Qdrant work against a document guaranteed to
                # already be searchable — `BULK_REFRESH=True` makes the
                # per-video write block until visible). The bulk
                # re-index that used to happen here on the full
                # `transcription_docs` list would just be re-writing
                # the exact same docs a second time — removed.
                es_transcriptions["indexed"]       = trans_stats.get("fetched_ok", 0)
                es_transcriptions["failed"]        = trans_stats.get("index_write_failed", 0)
                # Augment ES indexing counters with cache + fetch
                # breakdown so the Ingest hint can show
                # "N cached · M new · K failed".
                es_transcriptions["cached"]       = trans_stats.get("cached", 0)
                es_transcriptions["fetch_failed"] = trans_stats.get("fetched_failed", 0)
                es_transcriptions["no_transcript"] = trans_stats.get("no_transcript", 0)
                # Flush whatever's left below the domains.ycs.graph_builder.params.EXTRACT_CONCURRENCY
                # threshold — otherwise a tail of 1-4 videos would never
                # get a Neo4j task at all.
                _flush_neo4j_chunk()
                if _pending_track_tasks:
                    await asyncio.gather(*_pending_track_tasks, return_exceptions = True)
            finally:
                await domains.ycs.transcript.service.close_transcript_service()
        if extract_id:
            await _dispatch_streaming_totals(
                extract_id, video_ids, dispatched_ids,
                include_transcription,
            )
        return {
            "total_videos":      len(videos_dicts),
            "metadata":          es_metadata,
            "transcriptions":    es_transcriptions,
            # Surface in the final result too, so a JS poll that lands
            # only AFTER Phase 1 reaches SUCCESS (page reload mid-Phase-2/3,
            # late-tab visit) still gets titles to render the right
            # column with names instead of bare video_ids.
            "all_items":         all_items,
            "no_transcript_ids": no_transcript_ids,
        }
    finally:
        await es.close()


async def _extract_channel_async(
    channel_id:            str,
    max_results:           int,
    include_transcription: bool,
    languages:             list[str] | None,
) -> dict[str, Any]:
    es = _get_es_client()
    extractor = service.get_extractor()
    try:
        result = await extractor.extract_channel(channel_id, max_results)
        # ChannelResult schema → dict
        result_dict = (
            result.model_dump(exclude_none = False)
            if hasattr(result, "model_dump")
            else result
        )
        videos = result_dict.get("videos", [])
        videos_dicts = [
            v if isinstance(v, dict) else v.model_dump(exclude_none = False)
            for v in videos
        ]
        es_metadata = await domains.ycs.es_index.service.index_videos_to_elasticsearch(es, videos_dicts)
        es_transcriptions = {"indexed": 0, "failed": 0}
        if include_transcription:
            valid_ids = [
                v["id"] for v in videos_dicts
                if v.get("id") and "error" not in v
            ]
            channel_id_val = result_dict.get("channel_id")
            video_metadata = {
                v["id"]: {
                    "channel_id":  channel_id_val,
                    "playlist_id": v.get("playlist_id"),
                }
                for v in videos_dicts if v.get("id")
            }
            transcript_service = await domains.ycs.transcript.service.init_transcript_service(
                max_concurrent           = domains.ycs.transcript.params.MAX_CONCURRENT,
                browser_refresh_interval = 10,
                max_retries              = 3,
            )
            try:
                trans_stats: dict[str, int] = {}
                transcription_docs = await domains.ycs.transcript.service.fetch_transcriptions_batch(
                    valid_ids,
                    transcript_service = transcript_service,
                    es_client          = es,
                    languages          = languages,
                    video_metadata     = video_metadata,
                    stats              = trans_stats,
                )
                if transcription_docs:
                    es_transcriptions = await domains.ycs.es_index.service.index_transcriptions_to_elasticsearch(
                        es, transcription_docs,
                    )
                es_transcriptions["cached"]       = trans_stats.get("cached", 0)
                es_transcriptions["fetch_failed"] = trans_stats.get("fetched_failed", 0)
                es_transcriptions["no_transcript"] = trans_stats.get("no_transcript", 0)
            finally:
                await domains.ycs.transcript.service.close_transcript_service()
        return {
            "channel_id":     result_dict.get("channel_id"),
            "channel_name":   result_dict.get("channel_title"),
            "total_videos":   len(videos_dicts),
            "metadata":       es_metadata,
            "transcriptions": es_transcriptions,
        }
    finally:
        await es.close()


async def _extract_playlist_async(
    playlist_id:           str,
    max_results:           int,
    include_transcription: bool,
    languages:             list[str] | None,
) -> dict[str, Any]:
    es = _get_es_client()
    extractor = service.get_extractor()
    try:
        result = await extractor.extract_playlist(playlist_id, max_results)
        result_dict = (
            result.model_dump(exclude_none = False)
            if hasattr(result, "model_dump")
            else result
        )
        videos = result_dict.get("videos", [])
        videos_dicts = [
            v if isinstance(v, dict) else v.model_dump(exclude_none = False)
            for v in videos
        ]
        es_metadata = await domains.ycs.es_index.service.index_videos_to_elasticsearch(es, videos_dicts)
        es_transcriptions = {"indexed": 0, "failed": 0}
        if include_transcription:
            valid_ids = [
                v["id"] for v in videos_dicts
                if v.get("id") and "error" not in v
            ]
            playlist_id_val = result_dict.get("playlist_id")
            video_metadata = {
                v["id"]: {
                    "channel_id":  v.get("channel_id"),
                    "playlist_id": playlist_id_val,
                }
                for v in videos_dicts if v.get("id")
            }
            transcript_service = await domains.ycs.transcript.service.init_transcript_service(
                max_concurrent           = domains.ycs.transcript.params.MAX_CONCURRENT,
                browser_refresh_interval = 10,
                max_retries              = 3,
            )
            try:
                trans_stats: dict[str, int] = {}
                transcription_docs = await domains.ycs.transcript.service.fetch_transcriptions_batch(
                    valid_ids,
                    transcript_service = transcript_service,
                    es_client          = es,
                    languages          = languages,
                    video_metadata     = video_metadata,
                    stats              = trans_stats,
                )
                if transcription_docs:
                    es_transcriptions = await domains.ycs.es_index.service.index_transcriptions_to_elasticsearch(
                        es, transcription_docs,
                    )
                es_transcriptions["cached"]       = trans_stats.get("cached", 0)
                es_transcriptions["fetch_failed"] = trans_stats.get("fetched_failed", 0)
                es_transcriptions["no_transcript"] = trans_stats.get("no_transcript", 0)
            finally:
                await domains.ycs.transcript.service.close_transcript_service()
        return {
            "playlist_id":     result_dict.get("playlist_id"),
            "playlist_title":  result_dict.get("playlist_title"),
            "total_videos":    len(videos_dicts),
            "metadata":        es_metadata,
            "transcriptions":  es_transcriptions,
        }
    finally:
        await es.close()


# Celery tasks (sync wrappers — Celery is sync by default)
@app.task(
    bind = True,
    name = "domains.ycs.extract.task.extract_videos",
)
def extract_videos(
    self,
    video_ids:             list[str],
    include_transcription: bool             = True,
    languages:             list[str] | None = None,
) -> dict[str, Any]:
    """Extract metadata + transcripts for specific video IDs → ES."""
    logger.info(f"[extract_videos] Starting: {len(video_ids)} videos")
    self.update_state(
        state = "PROGRESS",
        meta  = {"phase": "init", "total": len(video_ids)},
    )

    def _progress(payload: dict[str, Any]) -> None:
        self.update_state(state = "PROGRESS", meta = payload)

    result = asyncio.run(
        _extract_videos_async(
            video_ids, include_transcription, languages,
            progress_cb = _progress,
            extract_id  = self.request.id,
        ),
    )
    logger.info(f"[extract_videos] Done: {result}")
    return result


@app.task(
    bind = True,
    name = "domains.ycs.extract.task.extract_channel",
)
def extract_channel(
    self,
    channel_id:            str,
    max_results:           int              = 0,
    include_transcription: bool             = True,
    languages:             list[str] | None = None,
) -> dict[str, Any]:
    """Extract all channel videos → ES (max_results=0 = all)."""
    logger.info(
        f"[extract_channel] Starting: {channel_id} (max={max_results})",
    )
    self.update_state(
        state = "PROGRESS",
        meta  = {"status": "extracting", "channel_id": channel_id},
    )
    result = asyncio.run(
        _extract_channel_async(
            channel_id, max_results, include_transcription, languages,
        ),
    )
    logger.info(
        f"[extract_channel] Done: {result.get('total_videos')} videos",
    )
    return result


@app.task(
    bind = True,
    name = "domains.ycs.extract.task.extract_playlist",
)
def extract_playlist(
    self,
    playlist_id:           str,
    max_results:           int              = 0,
    include_transcription: bool             = True,
    languages:             list[str] | None = None,
) -> dict[str, Any]:
    """Extract all playlist videos → ES (max_results=0 = all)."""
    logger.info(
        f"[extract_playlist] Starting: {playlist_id} (max={max_results})",
    )
    self.update_state(
        state = "PROGRESS",
        meta  = {"status": "extracting", "playlist_id": playlist_id},
    )
    result = asyncio.run(
        _extract_playlist_async(
            playlist_id, max_results, include_transcription, languages,
        ),
    )
    logger.info(
        f"[extract_playlist] Done: {result.get('total_videos')} videos",
    )
    return result
