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
    self.update_state(state = "PROGRESS", meta = {"status": "initializing"})

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
        groq_url = "https://api.groq.com/openai/v1"
        groq_key = os.environ.get("GROQ_API_KEY", "")
        nvidia_url = "https://integrate.api.nvidia.com/v1"
        nvidia_key = os.environ.get("NVIDIA_API_KEY", "")

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
        all_models.extend([
            _nim("z-ai/glm5"),
            _nim("moonshotai/kimi-k2.5"),
            _nim("moonshotai/kimi-k2-instruct"),
            _nim("deepseek-ai/deepseek-v3.2"),
            _nim("nvidia/llama-3.3-nemotron-super-49b-v1.5"),
            _nim("meta/llama-3.3-70b-instruct"),
            _nim("meta/llama-3.1-8b-instruct"),
        ])
        primary = all_models[0]
        llm = primary.with_fallbacks(all_models[1:])
        try:
            transcripts = await fetch_transcripts_from_es(es, video_ids)
            if not transcripts:
                return {"error": "No transcripts found in ES"}
            all_video_ids = list({t["video_id"] for t in transcripts})
            metadata_map = await fetch_metadata_from_es(es, all_video_ids)
            # Create Video/Channel nodes from metadata (no LLM cost)
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
