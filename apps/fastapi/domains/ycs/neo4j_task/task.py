"""ycs/neo4j_task — Celery: extract entities from FULL transcripts → Neo4j.

ONE task: `ingest_to_neo4j(video_ids?, batch_size=1)`.

Internally:
  1. Fresh AsyncElasticsearch (worker process)
  2. Fresh `Neo4jGraph` — deprecated did NOT pass `refresh_schema=False`
     here (only in app.py). Preserve that omission per port-fidelity.
  3. Pick a deployment from the unified LLM rotator via FGTS-VA bandit
     under `dd_process="ycs-neo4j"` (separate cell state from DD so
     DD prose variance doesn't drag down JSON-strong arms for entity
     extraction, and vice-versa). Bandit picks one model per Celery
     task (= one ingest run); all transcripts in this run share the
     pinned model. The 11-model ad-hoc `with_fallbacks` chain that
     previously lived here is GONE — it duplicated rotator policy
     (cooldown, BYOK selection, per-error retry) and bypassed the
     bandit entirely.
  4. Fetch transcripts + metadata from ES.
  5. `build_video_metadata_graph` — Video/Channel nodes (no LLM cost).
  6. `extract_and_store_graph` — LLM entity extraction with batching.
  7. Emit one bandit reward observation after the run completes (or
     bails). Aggregated per task — partial failure = failure reward."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from celery.utils.log import get_task_logger
from elasticsearch import AsyncElasticsearch
from langchain_neo4j import Neo4jGraph

from domains.llm.credentials import resolve_key
from domains.llm.rotator.chain import (
    build_ycs_neo4j_pinned_chain,
    pick_ycs_neo4j_deployment_bandit,
    record_ycs_neo4j_reward,
    release_ycs_provider_slot,
)
from domains.llm.rotator.chain.domain import classify_error
from domains.ycs.graph_builder import (
    build_video_metadata_graph,
    extract_and_store_graph,
    resolve_entities,
)
from domains.ycs.graph_builder.params import MAX_CONSECUTIVE_NONPRODUCTIVE
from domains.ycs.ingestion import (
    fetch_metadata_from_es,
    fetch_transcripts_from_es,
)
from infra.celery import app


logger = get_task_logger(__name__)

# 3 arm swaps = 4 arms total; 4 broken arms in a row means the provider side is down.
MAX_ARM_SWAPS = 3


@app.task(
    bind = True,
    name = "domains.ycs.neo4j_task.task.ingest_to_neo4j",
)
def ingest_to_neo4j(
    self,
    video_ids:  list[str] | None = None,
    batch_size: int              = 1,
) -> dict[str, Any]:
    """Extract entities from FULL transcripts via the rotator-bandit-pinned
    LLM → Neo4j. With `pipeline_task.NEO4J_BATCH_SIZE=1` each batch is one
    video, so per-video progress matches Phase 1 / Phase 2 granularity.

    Includes entity resolution post-processing via rapidfuzz."""
    logger.info(
        f"[ingest_to_neo4j] Starting: video_ids={video_ids}, "
        f"batch_size={batch_size}",
    )
    self.update_state(state = "PROGRESS", meta = {"phase": "init"})

    def _progress(payload: dict[str, Any]) -> None:
        self.update_state(state = "PROGRESS", meta = payload)

    async def _run() -> dict[str, Any]:
        from infra.langfuse import (
            set_current_span_langfuse_io,
            set_current_span_langfuse_observation_metadata,
            set_current_span_langfuse_trace_metadata,
        )
        from infra.langfuse.sessions import session as _lf_session
        from infra.otel import get_tracer
        with _lf_session(
            "ycs-ingest-neo4j",
            session_id = self.request.id or "(no-request-id)",
        ):
            with get_tracer().start_as_current_span(
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
                set_current_span_langfuse_io(input_data = {
                    "kind": "neo4j",
                    "video_ids_preview": list(video_ids or [])[:10],
                    "video_count": len(video_ids or []),
                    "batch_size": batch_size,
                    "task_id": self.request.id or "",
                })
                set_current_span_langfuse_trace_metadata({
                    "pipeline": "ycs_ingest",
                    "kind": "neo4j",
                    "task_id": self.request.id or "",
                    "video_count": len(video_ids or []),
                    "batch_size": batch_size,
                })
                set_current_span_langfuse_observation_metadata({
                    "kind": "neo4j",
                    "video_count": len(video_ids or []),
                })
                try:
                    result = await _run_inner()
                except Exception as e:
                    set_current_span_langfuse_io(output_data = {
                        "status": "failed",
                        "kind": "neo4j",
                        "task_id": self.request.id or "",
                        "error": f"{type(e).__name__}: {e}",
                    })
                    raise
                set_current_span_langfuse_io(output_data = {
                    "status": "done" if not result.get("error") else "failed",
                    "kind": "neo4j",
                    "task_id": self.request.id or "",
                    "result": result,
                })
                return result

    async def _run_inner() -> dict[str, Any]:
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
        if not any(resolve_key(env_var) for env_var in (
            "NVIDIA_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY",
            "MISTRAL_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY",
        )):
            return {
                "error": (
                    "No provider keys configured in the BYOK credential "
                    "store. Open /settings (LLM rotator) and paste at "
                    "least one provider key. Phase 3 entity extraction "
                    "can't proceed without it."
                ),
            }
        try:
            _progress({"phase": "fetching"})
            transcripts = await fetch_transcripts_from_es(es, video_ids)
            if not transcripts:
                return {"error": "No transcripts found in ES"}
            all_video_ids = list({t["video_id"] for t in transcripts})
            total_videos = len(all_video_ids)
            metadata_map = await fetch_metadata_from_es(es, all_video_ids)
            _progress({
                "phase": "metadata_graph",
                "total": total_videos,
            })
            video_metadata = [
                {**metadata_map.get(vid, {}), "video_id": vid}
                for vid in all_video_ids
            ]
            build_video_metadata_graph(neo4j_graph, video_metadata)
            # Cumulative progress adapter: segment-local callback restarts at 0/1; this keeps the bar advancing.
            completed_global: set[str] = set()
            try:
                rows = neo4j_graph.query(
                    "MATCH (d:Document) "
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
                    _progress(meta)
                    return
                if phase == "resolving":
                    meta = dict(payload)
                    meta["current"] = len(completed_global)
                    meta["total"] = total_videos
                    meta["completed_ids"] = _ordered(completed_global)
                    meta["failed_ids"] = []
                    _progress(meta)
                    return
                _progress(payload)
            seed = abs(hash(self.request.id or "")) & 0xFFFFFFFF
            tried: set[str] = set()
            arms_tried: list[str] = []
            agg_nodes = 0
            agg_rels = 0
            agg_attempted = 0
            agg_merged = 0
            pinned_model = ""
            extraction_stats: dict[str, Any] = {}
            for segment in range(MAX_ARM_SWAPS + 1):
                pinned_model, seg_provider, seg_slot = (
                    await pick_ycs_neo4j_deployment_bandit(
                        seed        = seed + segment,
                        video_count = len(all_video_ids),
                        exclude     = frozenset(tried),
                    )
                )
                tried.add(pinned_model)
                arms_tried.append(pinned_model)
                llm = build_ycs_neo4j_pinned_chain(pinned_model)
                logger.info(
                    f"[ingest_to_neo4j] pinned model: {pinned_model} "
                    f"(seed={seed}, segment={segment + 1}/"
                    f"{MAX_ARM_SWAPS + 1}, videos={len(all_video_ids)})"
                )
                t0 = time.monotonic()
                success = False
                error_class: str | None = None
                extraction_stats = {}
                try:
                    extraction_stats = await extract_and_store_graph(
                        transcripts  = transcripts,
                        metadata_map = metadata_map,
                        llm          = llm,
                        neo4j_graph  = neo4j_graph,
                        batch_size   = batch_size,
                        progress_cb  = _neo4j_progress,
                        abort_after_consecutive = MAX_CONSECUTIVE_NONPRODUCTIVE,
                        # Resolution is a global Neo4j pass — run it ONCE
                        # after the segment loop, not per segment.
                        run_resolution = False,
                    )
                    success = True
                except Exception as e:
                    error_class = classify_error(e)
                    logger.warning(
                        f"[ingest_to_neo4j] extraction failed for "
                        f"{pinned_model}: {type(e).__name__}: {e}"
                    )
                    raise
                finally:
                    latency_s = float(time.monotonic() - t0)
                    # Silent-zero guard: extract_and_store_graph swallows per-batch errors; 0-output = failure reward.
                    docs_processed   = int(extraction_stats.get("documents_processed", 0) or 0)
                    nodes_created    = int(extraction_stats.get("nodes_created", 0) or 0)
                    last_batch_error = extraction_stats.get("last_batch_error")
                    aborted          = bool(extraction_stats.get("aborted_nonproductive"))
                    silent_zero      = success and docs_processed > 0 and nodes_created == 0
                    effective_success = success and not silent_zero and not aborted
                    effective_err     = error_class
                    if aborted:
                        lbe = (last_batch_error or "").lower()
                        if "timeout" in lbe:
                            effective_err = "timeout"
                        elif ("ratelimit" in lbe or "rate limit" in lbe
                                or "429" in lbe):
                            effective_err = "rate_limit"
                        else:
                            effective_err = "schema_invalid"
                    elif silent_zero:
                        effective_err = "schema_invalid"
                        error_tail = (
                            f" Last LLM error: {last_batch_error}"
                            if last_batch_error
                            else " (no per-batch error recorded — LLM returned"
                                 " 0 entities cleanly; model likely passed"
                                 " schema validation but doesn't perform the"
                                 " extraction task)"
                        )
                        logger.warning(
                            f"[ingest_to_neo4j] silent-zero detected for "
                            f"{pinned_model}: {docs_processed} docs processed "
                            f"but 0 nodes created — recording NEGATIVE reward "
                            f"so the bandit stops re-picking this arm. Common "
                            f"cause: provider rejects LLMGraphTransformer's "
                            f"DynamicGraph schema (e.g. Groq + gpt-oss-120b)."
                            f"{error_tail}"
                        )
                    try:
                        await record_ycs_neo4j_reward(
                            deployment_id = pinned_model,
                            success       = effective_success,
                            latency_s     = latency_s,
                            error_class   = effective_err,
                            video_count   = len(all_video_ids),
                        )
                    except Exception as e:
                        logger.warning(
                            f"[ingest_to_neo4j] reward update failed: "
                            f"{type(e).__name__}: {e}"
                        )
                    # Release slot immediately; lingering for 1800s TTL saturated the pool mid-run.
                    try:
                        await release_ycs_provider_slot(seg_provider, seg_slot)
                    except Exception as e:
                        logger.warning(
                            f"[ingest_to_neo4j] slot release failed: "
                            f"{type(e).__name__}: {e}"
                        )
                agg_nodes     += int(extraction_stats.get("nodes_created", 0) or 0)
                agg_rels      += int(extraction_stats.get("relationships_created", 0) or 0)
                agg_attempted += int(extraction_stats.get("documents_processed", 0) or 0)
                videos_failed = int(extraction_stats.get("videos_failed", 0) or 0)
                if not (extraction_stats.get("aborted_nonproductive")
                        or silent_zero
                        or videos_failed > 0):
                    break
                if segment < MAX_ARM_SWAPS:
                    failed_ids_log = extraction_stats.get("failed_video_ids") or []
                    logger.warning(
                        f"[ingest_to_neo4j] arm {pinned_model}: "
                        f"{'circuit-break/silent-zero' if not videos_failed else f'{videos_failed} video(s) unprocessed'}"
                        f" — swapping arm for the remainder "
                        f"({segment + 1}/{MAX_ARM_SWAPS} swaps used, "
                        f"excluded: {sorted(tried)}, "
                        f"residual: {failed_ids_log[:10]})"
                    )
                else:
                    logger.error(
                        f"[ingest_to_neo4j] swap budget exhausted after "
                        f"{MAX_ARM_SWAPS + 1} arms ({sorted(tried)}) — "
                        f"giving up with partial results"
                    )
            # Entity resolution — ONCE after all segments (previously ran per-segment, 4× redundant).
            if agg_nodes > 0:
                _progress({
                    "phase": "resolving",
                    "nodes": agg_nodes,
                    "rels":  agg_rels,
                })
                logger.info("[ingest_to_neo4j] entity resolution starting")
                agg_merged = resolve_entities(neo4j_graph)
                logger.info(
                    f"[ingest_to_neo4j] entity resolution: "
                    f"{agg_merged} nodes merged"
                )
            return {
                "videos_processed": len(all_video_ids),
                "deployment":       pinned_model,
                **extraction_stats,
                "documents_processed":   agg_attempted,
                "nodes_created":         agg_nodes,
                "relationships_created": agg_rels,
                "entities_merged":       agg_merged,
                "arms_tried":            arms_tried,
                "arm_swaps":             len(arms_tried) - 1,
            }
        finally:
            await es.close()

    result = asyncio.run(_run())
    logger.info(f"[ingest_to_neo4j] Done: {result}")
    return result
