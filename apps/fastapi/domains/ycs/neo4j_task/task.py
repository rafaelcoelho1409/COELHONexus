"""ycs/neo4j_task — Celery: extract entities from FULL transcripts → Neo4j.

Direct port of deprecated `tasks/youtube/neo4j.py:L1-133`.

ONE task: `ingest_to_neo4j(video_ids?, batch_size=3)`.

Internally:
  1. Fresh AsyncElasticsearch (worker process)
  2. Fresh `Neo4jGraph` — deprecated did NOT pass `refresh_schema=False`
     here (only in app.py). Preserve that omission per port-fidelity.
  3. Build a 13-model `with_fallbacks` chain: Groq (speed) → NVIDIA NIM
     (capacity). Exact deprecated model list.
  4. Fetch transcripts + metadata from ES.
  5. `build_video_metadata_graph` — Video/Channel nodes (no LLM cost).
  6. `extract_and_store_graph` — LLM entity extraction with batching."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from celery.utils.log import get_task_logger
from elasticsearch import AsyncElasticsearch
from langchain_neo4j import Neo4jGraph
from langchain_openai import ChatOpenAI

from domains.llm.credentials import resolve_key
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
    batch_size: int              = 3,
) -> dict[str, Any]:
    """Extract entities from FULL transcripts via LLM → Neo4j.

    352 transcripts → 352 LLM calls (was 2911 with chunks). Includes
    entity resolution post-processing via rapidfuzz."""
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
        # LLM fallback chain: Groq (speed) → NVIDIA NIM (capacity).
        # Graph extraction uses 600s timeout (full transcripts are slow).
        # Keys come from the BYOK Fernet store via `resolve_key()` (not
        # `os.environ` directly — Helm secretEnv mappings for provider
        # keys were removed in the 2026-05-31 BYOK migration; reading
        # env returns "" and the OpenAI client raises
        # `AuthenticationError: Header of type 'authorization' was
        # missing`, killing the whole batch). Resolution happens at
        # chain-build time so a /settings update lands on the next
        # task invocation without a worker restart.
        groq_url   = "https://api.groq.com/openai/v1"
        groq_key   = resolve_key("GROQ_API_KEY")
        nvidia_url = "https://integrate.api.nvidia.com/v1"
        nvidia_key = resolve_key("NVIDIA_API_KEY")

        if not groq_key and not nvidia_key:
            return {
                "error": (
                    "No GROQ_API_KEY or NVIDIA_API_KEY configured in "
                    "the BYOK credential store. Open /settings (LLM "
                    "rotator) and paste at least one provider key. "
                    "Phase 3 entity extraction can't proceed without it."
                ),
            }

        def _groq(model: str) -> ChatOpenAI:
            return ChatOpenAI(
                model       = model,
                temperature = 0.0,
                base_url    = groq_url,
                api_key     = groq_key,
                max_retries = 0,
                timeout     = 120,
            )

        def _nim(model: str) -> ChatOpenAI:
            return ChatOpenAI(
                model       = model,
                temperature = 0.0,
                base_url    = nvidia_url,
                api_key     = nvidia_key,
                max_retries = 0,
                timeout     = 600,
            )

        all_models: list[ChatOpenAI] = []
        if groq_key:
            all_models.extend([
                _groq("llama-3.3-70b-versatile"),
                _groq("qwen/qwen3-32b"),
                _groq("llama-3.1-8b-instant"),
            ])
        # NIM (capacity) — 2026-06-07 refresh to match the canonical
        # rotator pool (`apps/fastapi/domains/llm/rotator/chain/service.py`
        # `_synth_entries` + `_all_entries`). Previously 5 of 7 model
        # ids were EOL or otherwise dropped from NIM (glm5 returned
        # HTTP 410 directly, the rest of the chain wasn't iterated by
        # `with_fallbacks` so Phase 3 silently produced 0 entities).
        # Order is Tier 1 frontier reasoning → Tier 2 non-reasoning →
        # Tier 3 cooldown — same ladder as the rotator's synth pool,
        # which the LLMGraphTransformer's structured-output workload
        # mirrors closely.
        if nvidia_key:
            all_models.extend([
                # Tier 1 — frontier reasoning, structured-output strong
                _nim("z-ai/glm-5.1"),                                 # was z-ai/glm5 (EOL 2026-05-18)
                _nim("moonshotai/kimi-k2.6"),                         # was kimi-k2.5 + kimi-k2-instruct
                _nim("minimaxai/minimax-m2.7"),                       # new (rotator SOTA)
                _nim("deepseek-ai/deepseek-v4-flash"),                # was deepseek-v3.2
                # Tier 2 — frontier non-reasoning fallback
                _nim("nvidia/nemotron-3-super-120b-a12b"),            # was llama-3.3-nemotron-super-49b-v1.5
                _nim("openai/gpt-oss-120b"),                          # new (rotator SOTA)
                _nim("mistralai/mistral-large-3-675b-instruct-2512"), # new (rotator SOTA)
                # Tier 3 — cooldown absorber
                _nim("meta/llama-4-maverick-17b-128e-instruct"),      # was llama-3.3-70b + llama-3.1-8b
            ])
        primary = all_models[0]
        llm = primary.with_fallbacks(all_models[1:])
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
            # Extract entities from FULL transcripts (not chunks)
            extraction_stats = await extract_and_store_graph(
                transcripts  = transcripts,
                metadata_map = metadata_map,
                llm          = llm,
                neo4j_graph  = neo4j_graph,
                batch_size   = batch_size,
                progress_cb  = _progress,
            )
            return {
                "videos_processed": len(all_video_ids),
                **extraction_stats,
            }
        finally:
            await es.close()

    result = asyncio.run(_run())
    logger.info(f"[ingest_to_neo4j] Done: {result}")
    return result
