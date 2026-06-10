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
)
from domains.llm.rotator.chain.domain import classify_error
from domains.ycs.graph_builder import (
    build_video_metadata_graph,
    extract_and_store_graph,
)
from domains.ycs.graph_builder.params import MAX_CONSECUTIVE_NONPRODUCTIVE
from domains.ycs.ingestion import (
    fetch_metadata_from_es,
    fetch_transcripts_from_es,
)
from infra.celery import app


logger = get_task_logger(__name__)

# Mid-run arm swaps (2026-06-09 circuit breaker). When the pinned arm
# circuit-breaks (MAX_CONSECUTIVE_NONPRODUCTIVE non-productive batches
# in a row), re-pick a different arm and continue — completed videos
# are skipped via the Neo4j video_id tag, failed ones get retried on
# the fresh arm. 3 swaps = 4 arms total; with ~12 arms in the pool and
# cross-provider slot caps forcing diversity, hitting 4 broken arms in
# a row means the provider side is down — at that point finishing the
# run on the last arm (however badly) beats cycling forever. Each dead
# arm costs ~9-10 min, so the worst case adds ~40 min to an overnight
# run instead of multiplying it by days.
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
        # BYOK preflight — the rotator's SYNTH_GROUP needs at least one
        # provider key resolvable to be useful for YCS. The factory itself
        # tolerates partial keying (LiteLLM Router cooldowns the rest),
        # but if NOTHING is set we want a clean, actionable error before
        # spending compute on the ES fetch.
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
            metadata_map = await fetch_metadata_from_es(es, all_video_ids)
            # Create Video/Channel nodes from metadata (no LLM cost)
            _progress({
                "phase": "metadata_graph",
                "total": len(all_video_ids),
            })
            video_metadata = [
                {**metadata_map.get(vid, {}), "video_id": vid}
                for vid in all_video_ids
            ]
            build_video_metadata_graph(neo4j_graph, video_metadata)
            # Rotator/bandit pick — one deployment per ARM SEGMENT.
            # `seed=hash(task_id)` so retries of the same task drift to
            # the bandit's current best (not a fixed seed) but two
            # different tasks land deterministically on their own picks.
            # When a segment circuit-breaks (MAX_CONSECUTIVE_NONPRODUCTIVE
            # non-productive batches), its NEGATIVE reward is recorded
            # immediately and the loop re-picks excluding every arm
            # already tried this run — extract_and_store_graph is
            # idempotent (completed videos skipped via the Neo4j
            # video_id tag) so the new segment resumes where the dead
            # arm stopped and retries its failures.
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
                pinned_model = await pick_ycs_neo4j_deployment_bandit(
                    seed        = seed + segment,
                    video_count = len(all_video_ids),
                    exclude     = frozenset(tried),
                )
                tried.add(pinned_model)
                arms_tried.append(pinned_model)
                llm = build_ycs_neo4j_pinned_chain(pinned_model)
                logger.info(
                    f"[ingest_to_neo4j] pinned model: {pinned_model} "
                    f"(seed={seed}, segment={segment + 1}/"
                    f"{MAX_ARM_SWAPS + 1}, videos={len(all_video_ids)})"
                )
                # Run extraction + reward update. Wall-clock and outcome
                # are PER SEGMENT so the bandit scores each arm on what
                # it actually did.
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
                        progress_cb  = _progress,
                        abort_after_consecutive = MAX_CONSECUTIVE_NONPRODUCTIVE,
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
                    # ---- Silent-zero guard ----
                    # `extract_and_store_graph` swallows per-batch LLM
                    # exceptions (BadRequest schema rejections, 5xx, etc.)
                    # so a model that fails EVERY batch still returns
                    # cleanly with nodes_created=0. Without this check the
                    # bandit would record reward=+0.6 for a 0-output run
                    # and then prefer the broken arm next time. We treat
                    # "processed N docs but produced 0 nodes" — and any
                    # circuit-breaker abort — as a failure for reward
                    # purposes.
                    docs_processed   = int(extraction_stats.get("documents_processed", 0) or 0)
                    nodes_created    = int(extraction_stats.get("nodes_created", 0) or 0)
                    last_batch_error = extraction_stats.get("last_batch_error")
                    aborted          = bool(extraction_stats.get("aborted_nonproductive"))
                    silent_zero      = success and docs_processed > 0 and nodes_created == 0
                    effective_success = success and not silent_zero and not aborted
                    effective_err     = error_class
                    if aborted:
                        # Timeout-storm arms break with a Timeout as the
                        # last batch error; 200-OK-empty arms break with
                        # none — map to the bandit's taxonomy accordingly.
                        effective_err = (
                            "timeout"
                            if "timeout" in (last_batch_error or "").lower()
                            else "schema_invalid"
                        )
                    elif silent_zero:
                        effective_err = "schema_invalid"
                        # Surface the actual last-batch LLM error body when
                        # available — without it the user only saw "0 nodes"
                        # with no provider-side diagnostic, making blocklist
                        # decisions blind.
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
                    # Best-effort reward emit. Swallowed errors don't fail
                    # the Celery task — the entity-extract work already
                    # succeeded (or failed) by this point; reward update
                    # is pure telemetry-for-the-bandit.
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
                agg_nodes     += int(extraction_stats.get("nodes_created", 0) or 0)
                agg_rels      += int(extraction_stats.get("relationships_created", 0) or 0)
                agg_attempted += int(extraction_stats.get("documents_processed", 0) or 0)
                agg_merged    += int(extraction_stats.get("entities_merged", 0) or 0)
                # Swap on circuit-break OR a fully-silent-zero segment.
                # The breaker needs MAX_CONSECUTIVE_NONPRODUCTIVE batches
                # to trip, so a run with FEWER transcripts than that
                # (e.g. 2 videos, both timing out) ends without ever
                # aborting — the silent-zero condition catches exactly
                # that and gives the small run its retry on a fresh arm
                # (idempotent: nothing got tagged, so all docs re-run).
                if not (extraction_stats.get("aborted_nonproductive") or silent_zero):
                    break
                if segment < MAX_ARM_SWAPS:
                    logger.warning(
                        f"[ingest_to_neo4j] arm {pinned_model} produced "
                        f"nothing (circuit-break/silent-zero) — swapping "
                        f"({segment + 1}/{MAX_ARM_SWAPS} swaps used, "
                        f"excluded: {sorted(tried)})"
                    )
                else:
                    logger.error(
                        f"[ingest_to_neo4j] all {MAX_ARM_SWAPS + 1} arms "
                        f"produced nothing ({sorted(tried)}) — provider "
                        f"side likely degraded; giving up with partial "
                        f"results"
                    )
            return {
                "videos_processed": len(all_video_ids),
                "deployment":       pinned_model,
                **extraction_stats,
                # Cross-segment aggregates — per-arm stats above reflect
                # only the FINAL segment; these cover the whole run.
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
