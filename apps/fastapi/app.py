import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from routers.v1.youtube import agents as youtube_agents
from routers.v1.youtube import models as youtube_models
from routers.v1.youtube import search as youtube_search

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
    app.state.config = {
        "configurable": {"thread_id": "1"}
    }
    # Async Redis checkpointer - yield INSIDE context manager!
    async with AsyncRedisSaver.from_conn_string(REDIS_URL) as checkpointer:
        await checkpointer.setup()
        app.state.checkpointer = checkpointer
        print("Redis checkpointer initialized.", flush = True)
        print("FastAPI startup complete.", flush = True)
        yield  # App runs here - connection stays open
        print("FastAPI shutting down...", flush = True)
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
    youtube_models.router,
    prefix = "/api/v1/youtube/models",
    tags = ["YouTube"],
)

app.include_router(
    youtube_search.router,
    prefix = "/api/v1/youtube/search",
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