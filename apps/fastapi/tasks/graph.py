"""
Graph Tasks — Neo4j Knowledge Graph Entity Extraction (Optimized)

IMPROVEMENTS:
- Full transcript per LLM call (352 calls instead of 2911)
- Domain-specific schema + extraction instructions
- Entity resolution via rapidfuzz (post-processing)
- Rate-limit pacing (2s between batches, batch_size=3)
"""
import asyncio
import os
import sys
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from celery_app import app
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@app.task(
    bind = True, 
    name = "tasks.graph.ingest_to_graph")
def ingest_to_graph(
    self, 
    video_ids = None, 
    batch_size = 3):
    """
    Extract entities from FULL transcripts via LLM → store in Neo4j.

    Optimized: sends full transcript per LLM call (not chunks).
    352 transcripts = 352 LLM calls (was 2911 with chunks).
    Includes entity resolution post-processing.
    """
    logger.info(f"[ingest_to_graph] Starting: video_ids={video_ids}, batch_size={batch_size}")
    self.update_state(
        state = "PROGRESS", 
        meta = {"status": "initializing"})

    async def _run():
        from elasticsearch import AsyncElasticsearch
        from langchain_neo4j import Neo4jGraph
        from langchain_openai import ChatOpenAI
        from services.ingestion import fetch_transcripts_from_es, fetch_metadata_from_es
        from services.graph_builder import extract_and_store_graph, build_video_metadata_graph
        es = AsyncElasticsearch(
            hosts = [os.environ["ELASTICSEARCH_HOST"]],
            basic_auth = (
                os.environ["ELASTICSEARCH_USERNAME"],
                os.environ.get("ELASTICSEARCH_PASSWORD", ""),
            ),
            verify_certs = False,
        )
        neo4j_graph = Neo4jGraph(
            url = os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            username = os.environ.get("NEO4J_USERNAME", "neo4j"),
            password = os.environ.get("NEO4J_PASSWORD", ""),
        )
        # LLM fallback chain: Groq (speed) → NVIDIA NIM (capacity)
        # Graph extraction uses 600s timeout (full transcripts are slow)
        GROQ_URL = "https://api.groq.com/openai/v1"
        GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
        NVIDIA_URL = "https://integrate.api.nvidia.com/v1"
        NVIDIA_KEY = os.environ.get("NVIDIA_API_KEY", "")

        def _groq(model):
            return ChatOpenAI(
                model = model, 
                temperature = 0.0,
                base_url = GROQ_URL, 
                api_key = GROQ_KEY,
                max_retries = 0, 
                timeout = 120,
            )

        def _nim(model):
            return ChatOpenAI(
                model = model, 
                temperature = 0.0,
                base_url = NVIDIA_URL, 
                api_key = NVIDIA_KEY,
                max_retries = 0, 
                timeout = 600,
            )
        all_models = []
        if GROQ_KEY:
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
            # 1. Fetch transcripts and metadata from ES
            transcripts = await fetch_transcripts_from_es(es, video_ids)
            if not transcripts:
                return {"error": "No transcripts found in ES"}
            all_video_ids = list({t["video_id"] for t in transcripts})
            metadata_map = await fetch_metadata_from_es(es, all_video_ids)
            # 2. Create Video/Channel nodes from metadata (no LLM cost)
            video_metadata = [
                {**metadata_map.get(vid, {}), "video_id": vid}
                for vid in all_video_ids
            ]
            build_video_metadata_graph(neo4j_graph, video_metadata)
            # 3. Extract entities from FULL transcripts (not chunks)
            extraction_stats = await extract_and_store_graph(
                transcripts = transcripts,
                metadata_map = metadata_map,
                llm = llm,
                neo4j_graph = neo4j_graph,
                batch_size = batch_size,
            )
            return {
                "videos_processed": len(all_video_ids),
                **extraction_stats,
            }
        finally:
            await es.close()
    result = asyncio.run(_run())
    logger.info(f"[ingest_to_graph] Done: {result}")
    return result
