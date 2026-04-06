import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import redis.asyncio as redis_aio
from elasticsearch import AsyncElasticsearch
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from routers.v1.youtube import agents as youtube_agents
from routers.v1.youtube import content as youtube_content
from routers.v1.youtube.helpers import (
    create_youtube_indexes,
    init_transcript_service,
    close_transcript_service,
)

# =============================================================================
# Configuration
# =============================================================================
REDIS_HOST = os.environ["REDIS_HOST"]
REDIS_PORT = os.environ["REDIS_PORT"]
REDIS_PASSWORD = os.environ["REDIS_PASSWORD"]

# Build Redis URL with optional authentication
if REDIS_PASSWORD:
    REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}"
else:
    REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}"

# ElasticSearch configuration
ES_HOST = os.environ["ELASTICSEARCH_HOST"]
ES_USERNAME = os.environ["ELASTICSEARCH_USERNAME"]
ES_PASSWORD = os.environ["ELASTICSEARCH_PASSWORD"]

# =============================================================================
# Lifespan (startup/shutdown)
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown tasks."""
    print("Starting FastAPI Service...", flush = True)
    app.state.redis_aio = redis_aio.from_url(REDIS_URL)
    # ElasticSearch async client
    app.state.es = AsyncElasticsearch(
        hosts = [ES_HOST],
        basic_auth = (ES_USERNAME, ES_PASSWORD) if ES_PASSWORD else None,
        verify_certs = False,  # Tailscale provides encryption
    )
    print(f"ElasticSearch client initialized: {ES_HOST}", flush=True)
    # Create YouTube indexes if not exists (metadata + transcriptions)
    es_index_result = await create_youtube_indexes(app.state.es)
    print(f"ElasticSearch YouTube indexes: {es_index_result}", flush=True)
    # Initialize Playwright transcript service (browser pool)
    # v5 optimizations (overnight-safe):
    # - max_concurrent=5: Optimal with cleanup safeguards
    # - browser_refresh_interval=10: Aggressive refresh to release memory
    # - max_retries=5: More retries for overnight batch jobs
    app.state.transcript_service = await init_transcript_service(
        max_concurrent=5,
        browser_refresh_interval=10,
        max_retries=5,
    )
    print("Playwright transcript service initialized.", flush=True)
    app.state.config = {
        "configurable": {"thread_id": "1"}
    }
    app.state.llm_framework = {
        "NVIDIA": ChatOpenAI,
    }
    # Load LLM config from Redis or use defaults
    config_key = "coelhonexus:youtube:agents:config"
    llm_config = await app.state.redis_aio.json().get(config_key)
    if llm_config:
        # Config exists - use it to instantiate LLM
        provider = llm_config["provider"]
        llm_class = app.state.llm_framework[provider]
        app.state.llm = llm_class(
            model = llm_config["model"],
            temperature = llm_config["temperature"],
            base_url = llm_config["base_url"],
            api_key = llm_config["api_key"],
        )
        print(f"LLM loaded from Redis: {provider}/{llm_config['model']}", flush=True)
    else:
        # No config - use default
        app.state.llm = ChatOpenAI(
            model = "meta/llama-3.3-70b-instruct",
            temperature = 0.0,
            base_url = "https://integrate.api.nvidia.com/v1",
            api_key = os.environ["NVIDIA_API_KEY"],
        )
        print("LLM loaded with defaults (NVIDIA/llama-3.3-70b)", flush = True)
    # Async Redis checkpointer - yield INSIDE context manager!
    async with AsyncRedisSaver.from_conn_string(REDIS_URL) as checkpointer:
        await checkpointer.setup()
        app.state.checkpointer = checkpointer
        print("Redis checkpointer initialized.", flush = True)
        print("FastAPI startup complete.", flush = True)
        yield  # App runs here - connection stays open
        print("FastAPI shutting down...", flush = True)
        await close_transcript_service()
        print("Playwright transcript service closed.", flush=True)
        await app.state.es.close()
        print("ElasticSearch connection closed.", flush=True)
        await app.state.redis_aio.close()
    print("Redis connection closed.", flush = True)


# =============================================================================
# FastAPI App
# =============================================================================
app = FastAPI(
    title = "COELHO Nexus - FastAPI",
    description = "COELHO Nexus - FastAPI",
    version = "1.0.0",
    lifespan = lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins = ["*"],
    allow_credentials = True,
    allow_methods = ["*"],
    allow_headers = ["*"],
)


# =============================================================================
# Routers
# =============================================================================
app.include_router(
    youtube_agents.router,
    prefix = "/api/v1/youtube/agents",
    tags = ["YouTube"],
)

app.include_router(
    youtube_content.router,
    prefix = "/api/v1/youtube/content",
    tags = ["YouTube"],
)


# =============================================================================
# Root Endpoints
# =============================================================================
@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "service": "FastAPI Service - COELHO Nexus",
        "version": "1.0.0",
        "endpoints": {
            "docs": "/docs",
            "health": "/health",
        },
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "COELHO Nexus"}