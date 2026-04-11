"""
Graph Tasks — Neo4j Knowledge Graph Entity Extraction

CONCEPT: Wraps LLMGraphTransformer entity extraction (services/graph_builder.py)
as a Celery task. Runs in the LLM worker — each chunk requires an LLM call.

This was the most expensive operation (100+ LLM calls, 5+ minutes).
As a Celery task, it runs in the background with progress tracking.
"""
import asyncio
import os
from celery_app import app
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@app.task(bind = True, name = "tasks.graph.ingest_to_graph")
def ingest_to_graph(self, video_ids = None, batch_size = 5, chunk_size = 2000, chunk_overlap = 200):
    """
    Extract entities from transcript chunks via LLM → store in Neo4j.

    Two-phase process:
    1. Create Video/Channel nodes from metadata (free — no LLM)
    2. Extract Topic/Person/Technology entities from chunks (LLM-expensive)
    """
    logger.info(f"[ingest_to_graph] Starting: video_ids={video_ids}, batch_size={batch_size}")
    self.update_state(state = "PROGRESS", meta = {"status": "initializing"})

    async def _run():
        from elasticsearch import AsyncElasticsearch
        from langchain_neo4j import Neo4jGraph
        from langchain_openai import ChatOpenAI
        from services.ingestion import fetch_transcripts_from_es, fetch_metadata_from_es
        from services.chunker import create_chunker, chunk_transcript
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

        # Use the same LLM fallback chain as FastAPI
        NVIDIA_URL = "https://integrate.api.nvidia.com/v1"
        NVIDIA_KEY = os.environ.get("NVIDIA_API_KEY", "")

        def _nim(model):
            return ChatOpenAI(
                model = model, temperature = 0.0,
                base_url = NVIDIA_URL, api_key = NVIDIA_KEY,
                max_retries = 0, timeout = 60,
            )

        primary = _nim("z-ai/glm5")
        fallbacks = [
            _nim("moonshotai/kimi-k2.5"),
            _nim("moonshotai/kimi-k2-instruct"),
            _nim("deepseek-ai/deepseek-v3.2"),
            _nim("nvidia/llama-3.3-nemotron-super-49b-v1.5"),
            _nim("meta/llama-3.3-70b-instruct"),
            _nim("meta/llama-3.1-8b-instruct"),
        ]
        llm = primary.with_fallbacks(fallbacks)

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

            # 3. Chunk transcripts
            chunker = create_chunker(chunk_size, chunk_overlap)
            all_chunks = []
            for transcript in transcripts:
                vid = transcript["video_id"]
                meta = metadata_map.get(vid, {})
                chunks = chunk_transcript(
                    video_id = vid,
                    content = transcript.get("content", ""),
                    metadata = {
                        "title": meta.get("title", ""),
                        "channel": meta.get("channel", ""),
                    },
                    chunker = chunker,
                )
                all_chunks.extend(chunks)

            # 4. Extract entities via LLM and store in Neo4j
            extraction_stats = await extract_and_store_graph(
                documents = all_chunks,
                llm = llm,
                neo4j_graph = neo4j_graph,
                batch_size = batch_size,
            )

            return {
                "videos_processed": len(all_video_ids),
                "chunks_processed": len(all_chunks),
                **extraction_stats,
            }
        finally:
            await es.close()

    result = asyncio.run(_run())
    logger.info(f"[ingest_to_graph] Done: {result}")
    return result
