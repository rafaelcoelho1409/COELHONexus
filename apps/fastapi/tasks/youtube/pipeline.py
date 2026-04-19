"""
Pipeline Tasks — Full channel ingestion chain

CONCEPT: Celery chains execute tasks in sequence, passing results forward.
One POST /pipeline call triggers the entire flow:
  extract_channel → ingest_to_qdrant → ingest_to_graph → invalidate_cache

If any step fails, Celery retries that step — not the whole pipeline.
"""
from celery import chain
from celery_app import app
from tasks.youtube.crawler import extract_channel
from tasks.youtube.ingestion import ingest_to_qdrant, invalidate_cache
from tasks.youtube.graph import ingest_to_graph


@app.task(bind = True, name = "tasks.youtube.pipeline.full_channel_pipeline")
def full_channel_pipeline(
    self,
    channel_id,
    max_results = 0,
    include_transcription = True,
    include_qdrant = True,
    include_graph = False,
):
    """
    Full pipeline: Extract → Qdrant vectors → Neo4j graph → Clear cache.

    CONCEPT: chain() links tasks sequentially. Each task runs in its
    own worker (possibly on different queues):
      extract_channel (crawler queue) →
      ingest_to_qdrant (embedding queue) →
      ingest_to_graph (llm queue) →
      invalidate_cache (embedding queue)

    Args:
        channel_id: YouTube channel ID or @handle
        max_results: 0 = all videos
        include_transcription: Extract transcripts via Playwright
        include_qdrant: Ingest to Qdrant vector store
        include_graph: Extract entities to Neo4j (expensive — LLM calls)
    """
    steps = [extract_channel.si(channel_id, max_results, include_transcription)]
    if include_qdrant:
        steps.append(ingest_to_qdrant.si())
    if include_graph:
        steps.append(ingest_to_graph.si())
    steps.append(invalidate_cache.si())
    # si() = signature with immutable args (don't pass previous result as first arg)
    pipeline = chain(*steps)
    result = pipeline.apply_async()
    return {
        "pipeline_id": result.id,
        "steps": [s.name for s in steps],
        "channel_id": channel_id,
    }
