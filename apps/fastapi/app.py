import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import redis.asyncio as redis_aio
from elasticsearch import AsyncElasticsearch
from qdrant_client import AsyncQdrantClient
from neo4j import AsyncGraphDatabase
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from routers.v1.youtube import agents as youtube_agents
from routers.v1.youtube import content as youtube_content
from routers.v1 import tasks as tasks_router
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

# Qdrant configuration
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY")

# Neo4j configuration
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.environ.get("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")

# PostgreSQL configuration (LangGraph conversation persistence)
PG_HOST = os.environ.get("POSTGRES_HOST", "postgresql.postgresql.svc.cluster.local")
PG_PORT = os.environ.get("POSTGRES_PORT", "5432")
PG_USER = os.environ.get("POSTGRES_USER", "postgres")
PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
PG_DATABASE = os.environ.get("POSTGRES_DATABASE", "coelhonexus")
from urllib.parse import quote_plus as _urlencode
PG_URL = f"postgresql://{PG_USER}:{_urlencode(PG_PASSWORD)}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"


async def _ensure_postgres_database():
    """
    Auto-create the PostgreSQL database if it doesn't exist.
    Connects to the default 'postgres' database first, then creates the target.
    Uses psycopg (bundled with langgraph-checkpoint-postgres).
    """
    import psycopg
    admin_url = f"postgresql://{PG_USER}:{_urlencode(PG_PASSWORD)}@{PG_HOST}:{PG_PORT}/postgres"
    try:
        # autocommit=True required for CREATE DATABASE (can't run inside transaction)
        async with await psycopg.AsyncConnection.connect(admin_url, autocommit=True) as conn:
            result = await conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (PG_DATABASE,)
            )
            exists = await result.fetchone()
            if not exists:
                await conn.execute(f'CREATE DATABASE "{PG_DATABASE}"')
                print(f"PostgreSQL database '{PG_DATABASE}' created.", flush=True)
            else:
                print(f"PostgreSQL database '{PG_DATABASE}' already exists.", flush=True)
    except Exception as e:
        print(f"PostgreSQL database check/create failed: {e}", flush=True)

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
    # - max_retries=3: Balance between recovery and avoiding wasted retries
    app.state.transcript_service = await init_transcript_service(
        max_concurrent=5,
        browser_refresh_interval=10,
        max_retries=3,
    )
    print("Playwright transcript service initialized.", flush=True)
    # Qdrant async client
    app.state.qdrant = AsyncQdrantClient(
        url=QDRANT_URL,
        port=QDRANT_PORT,
        api_key=QDRANT_API_KEY if QDRANT_API_KEY else None,
    )
    qdrant_collections = await app.state.qdrant.get_collections()
    print(f"Qdrant connected: {len(qdrant_collections.collections)} collections", flush=True)
    # Neo4j async driver
    app.state.neo4j_driver = AsyncGraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USERNAME, NEO4J_PASSWORD) if NEO4J_PASSWORD else None,
    )
    await app.state.neo4j_driver.verify_connectivity()
    print(f"Neo4j connected: {NEO4J_URI}", flush=True)
    # Embedding models: LAZY-LOADED on first use (not at startup)
    # Loading bge-base (~430MB) + BM25 sparse at startup caused OOMKilled (4Gi limit)
    # with Playwright browser pool (5 contexts) already in memory.
    # Models are loaded on first /ingest or /search call instead.
    app.state.dense_embeddings = None
    app.state.sparse_embeddings = None
    print("Embedding models will lazy-load on first use.", flush=True)
    # Neo4j LangChain graph (for LLMGraphTransformer and Cypher queries)
    # This is separate from neo4j_driver — Neo4jGraph wraps it with LangChain integration
    from langchain_neo4j import Neo4jGraph
    app.state.neo4j_graph = Neo4jGraph(
        url = NEO4J_URI,
        username = NEO4J_USERNAME,
        password = NEO4J_PASSWORD,
    )
    print("Neo4j LangChain graph initialized.", flush=True)
    app.state.config = {
        "configurable": {"thread_id": "1"}
    }
    # =========================================================================
    # LLM with Fallbacks — Groq-first + NVIDIA NIM (April 2026)
    # =========================================================================
    # CONCEPT: Groq models respond in milliseconds (394-1000 TPS on custom
    # LPU hardware) vs seconds on NVIDIA NIM. We put Groq FIRST for speed,
    # then fall back to NVIDIA NIM's 14 models when Groq hits rate limits.
    #
    # Groq free tier: 30 RPM per model (60 for Qwen3/Kimi), 100K-500K TPD
    # NVIDIA NIM free tier: ~40 RPM per model, unlimited daily tokens
    # Combined: Groq handles ~150 RPM (5 models), NVIDIA handles overflow
    #
    # max_retries=0: fail immediately on 429 (don't waste time retrying)
    # timeout=60: don't hang on slow models (Groq rarely needs >5s)
    #
    # === Groq models (speed-first, 394-1000 TPS) ===
    #  1. Llama 3.3 70B      — Best quality on Groq, 394 TPS, 30 RPM
    #  2. Qwen3 32B          — 60 RPM (double!), 662 TPS, reasoning support
    #  3. Kimi K2 Instruct   — 60 RPM, strong quality
    #  4. GPT-OSS 120B       — OpenAI open-source, 500 TPS
    #  5. Llama 3.1 8B       — 840 TPS, highest free quota (500K TPD)
    #
    # === NVIDIA NIM models (unlimited fallback, ranked by Arena ELO) ===
    #  6. GLM5               — Arena 1451, best open-source
    #  7. Kimi K2.5          — Arena 1447, multimodal
    #  8. Kimi K2 Instruct   — Arena 1447, text-only
    #  9. DeepSeek V3.2      — Arena 1421, MIT-licensed
    # 10. Nemotron Super 120B
    # 11. Qwen 3.5 122B
    # 12. Nemotron Super 49B
    # 13. Mistral Small 4
    # 14. Gemma 4 31B
    # 15. Llama 4 Maverick
    # 16. Llama 3.3 70B
    # 17. Qwen3 Next 80B
    # 18. Llama 3.1 8B
    #
    GROQ_URL = "https://api.groq.com/openai/v1"
    GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
    NVIDIA_URL = "https://integrate.api.nvidia.com/v1"
    NVIDIA_KEY = os.environ.get("NVIDIA_API_KEY", "")

    def _groq(model: str) -> ChatOpenAI:
        return ChatOpenAI(
            model = model,
            temperature = 0.0,
            base_url = GROQ_URL,
            api_key = GROQ_KEY,
            max_retries = 0,
            timeout = 30,  # Groq is fast — 30s is generous
        )

    def _nim(model: str) -> ChatOpenAI:
        return ChatOpenAI(
            model = model,
            temperature = 0.0,
            base_url = NVIDIA_URL,
            api_key = NVIDIA_KEY,
            max_retries = 0,
            timeout = 60,
        )

    # Build fallback chain: Groq (speed) → NVIDIA NIM (capacity)
    groq_models = []
    if GROQ_KEY:
        groq_models = [
            _groq("llama-3.3-70b-versatile"),
            _groq("qwen/qwen3-32b"),
            _groq("moonshotai/kimi-k2-instruct"),
            _groq("openai/gpt-oss-120b"),
            _groq("llama-3.1-8b-instant"),
        ]

    nim_models = [
        _nim("z-ai/glm5"),
        _nim("moonshotai/kimi-k2.5"),
        _nim("moonshotai/kimi-k2-instruct"),
        _nim("moonshotai/kimi-k2-thinking"),
        _nim("deepseek-ai/deepseek-v3.2"),
        _nim("nvidia/nemotron-3-super-120b-a12b"),
        _nim("qwen/qwen3.5-122b-a10b"),
        _nim("nvidia/llama-3.3-nemotron-super-49b-v1.5"),
        _nim("mistralai/mistral-small-4-119b-2603"),
        _nim("google/gemma-4-31b-it"),
        _nim("meta/llama-4-maverick-17b-128e-instruct"),
        _nim("meta/llama-3.3-70b-instruct"),
        _nim("qwen/qwen3-next-80b-a3b-instruct"),
        _nim("meta/llama-3.1-8b-instruct"),
    ]

    all_models = groq_models + nim_models
    primary = all_models[0]
    fallbacks = all_models[1:]
    app.state.llm = primary.with_fallbacks(fallbacks)
    providers = f"Groq ({len(groq_models)})" if groq_models else ""
    providers += (" + " if providers else "") + f"NVIDIA NIM ({len(nim_models)})"
    print(f"LLM loaded: {primary.model_name} + {len(fallbacks)} fallbacks ({providers})", flush = True)
    # PostgreSQL checkpointer for conversation persistence
    # Auto-creates the database if it doesn't exist
    await _ensure_postgres_database()
    async with AsyncPostgresSaver.from_conn_string(PG_URL) as checkpointer:
        await checkpointer.setup()
        app.state.checkpointer = checkpointer
        print(f"PostgreSQL checkpointer initialized: {PG_HOST}/{PG_DATABASE}", flush = True)
        print("FastAPI startup complete.", flush = True)
        yield  # App runs here - connection stays open
        print("FastAPI shutting down...", flush = True)
        await close_transcript_service()
        print("Playwright transcript service closed.", flush=True)
        await app.state.qdrant.close()
        print("Qdrant connection closed.", flush=True)
        await app.state.neo4j_driver.close()
        print("Neo4j connection closed.", flush=True)
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

app.include_router(
    tasks_router.router,
    prefix = "/api/v1/tasks",
    tags = ["Tasks"],
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