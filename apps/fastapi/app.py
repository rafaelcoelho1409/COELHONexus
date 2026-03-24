import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import redis.asyncio as redis_aio
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from schemas.inputs import YouTubeSearchConfig
from routers.v1.youtube import agents as youtube_agents
from routers.v1.youtube import content as youtube_content

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

# =============================================================================
# Lifespan (startup/shutdown)
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown tasks."""
    print("Starting FastAPI Service...", flush = True)
    app.state.redis_aio = redis_aio.from_url(REDIS_URL)
    # Create default YouTubeSearchConfig only if it doesn't exist
    search_config_key = "coelhonexus:youtube:search:config"
    existing_config = await app.state.redis_aio.json().get(search_config_key)
    if not existing_config:
        search_config = YouTubeSearchConfig()
        search_config = search_config.model_dump(exclude_none = True)
        search_config.setdefault("query", "alborghetti")
        search_config.setdefault("max_results", 10)
        search_config.setdefault("sort_by", "Relevance")
        await app.state.redis_aio.json().set(
            search_config_key,
            "$",
            search_config
        )
        print("YouTubeSearchConfig created with defaults.", flush = True)
    else:
        print("YouTubeSearchConfig loaded from Redis.", flush = True)
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