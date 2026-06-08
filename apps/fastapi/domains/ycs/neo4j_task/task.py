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
from domains.ycs.ingestion import (
    fetch_metadata_from_es,
    fetch_transcripts_from_es,
)
from infra.celery import app


logger = get_task_logger(__name__)


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
            # Rotator/bandit pick — one deployment for this entire task.
            # `seed=hash(task_id)` so retries of the same task drift to
            # the bandit's current best (not a fixed seed) but two
            # different tasks land deterministically on their own picks.
            seed = abs(hash(self.request.id or "")) & 0xFFFFFFFF
            pinned_model = await pick_ycs_neo4j_deployment_bandit(
                seed        = seed,
                video_count = len(all_video_ids),
            )
            llm = build_ycs_neo4j_pinned_chain(pinned_model)
            logger.info(
                f"[ingest_to_neo4j] pinned model: {pinned_model} "
                f"(seed={seed}, videos={len(all_video_ids)})"
            )
            # Run extraction + reward update. We track wall-clock and
            # outcome so the bandit can score this arm for ycs-neo4j.
            t0 = time.monotonic()
            success = False
            error_class: str | None = None
            extraction_stats: dict[str, Any] = {}
            try:
                extraction_stats = await extract_and_store_graph(
                    transcripts  = transcripts,
                    metadata_map = metadata_map,
                    llm          = llm,
                    neo4j_graph  = neo4j_graph,
                    batch_size   = batch_size,
                    progress_cb  = _progress,
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
                # Best-effort reward emit. Swallowed errors don't fail
                # the Celery task — the entity-extract work already
                # succeeded (or failed) by this point; reward update
                # is pure telemetry-for-the-bandit.
                try:
                    await record_ycs_neo4j_reward(
                        deployment_id = pinned_model,
                        success       = success,
                        latency_s     = latency_s,
                        error_class   = error_class,
                        video_count   = len(all_video_ids),
                    )
                except Exception as e:
                    logger.warning(
                        f"[ingest_to_neo4j] reward update failed: "
                        f"{type(e).__name__}: {e}"
                    )
            return {
                "videos_processed": len(all_video_ids),
                "deployment":       pinned_model,
                **extraction_stats,
            }
        finally:
            await es.close()

    result = asyncio.run(_run())
    logger.info(f"[ingest_to_neo4j] Done: {result}")
    return result
