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
from routers.v1.knowledge import distiller as knowledge_distiller
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
        async with await psycopg.AsyncConnection.connect(
            admin_url, 
            autocommit = True) as conn:
            result = await conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (PG_DATABASE,)
            )
            exists = await result.fetchone()
            if not exists:
                await conn.execute(f'CREATE DATABASE "{PG_DATABASE}"')
                print(f"PostgreSQL database '{PG_DATABASE}' created.", flush = True)
            else:
                print(f"PostgreSQL database '{PG_DATABASE}' already exists.", flush = True)
    except Exception as e:
        print(f"PostgreSQL database check/create failed: {e}", flush = True)

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
    print(f"ElasticSearch client initialized: {ES_HOST}", flush = True)
    # Create YouTube indexes if not exists (metadata + transcriptions)
    es_index_result = await create_youtube_indexes(app.state.es)
    print(f"ElasticSearch YouTube indexes: {es_index_result}", flush = True)
    # Initialize Playwright transcript service (browser pool)
    # v5 optimizations (overnight-safe):
    # - max_concurrent=5: Optimal with cleanup safeguards
    # - browser_refresh_interval=10: Aggressive refresh to release memory
    # - max_retries=3: Balance between recovery and avoiding wasted retries
    app.state.transcript_service = await init_transcript_service(
        max_concurrent = 5,
        browser_refresh_interval = 10,
        max_retries = 3,
    )
    print("Playwright transcript service initialized.", flush = True)
    # Qdrant async client
    app.state.qdrant = AsyncQdrantClient(
        url = QDRANT_URL,
        port = QDRANT_PORT,
        api_key = QDRANT_API_KEY if QDRANT_API_KEY else None,
    )
    qdrant_collections = await app.state.qdrant.get_collections()
    print(f"Qdrant connected: {len(qdrant_collections.collections)} collections", flush = True)
    # Neo4j async driver
    app.state.neo4j_driver = AsyncGraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USERNAME, NEO4J_PASSWORD) if NEO4J_PASSWORD else None,
    )
    await app.state.neo4j_driver.verify_connectivity()
    print(f"Neo4j connected: {NEO4J_URI}", flush = True)
    # Embedding models: LAZY-LOADED on first use (not at startup)
    # Loading bge-base (~430MB) + BM25 sparse at startup caused OOMKilled (4Gi limit)
    # with Playwright browser pool (5 contexts) already in memory.
    # Models are loaded on first /ingest or /search call instead.
    app.state.dense_embeddings = None
    app.state.sparse_embeddings = None
    print("Embedding models will lazy-load on first use.", flush = True)
    # Neo4j LangChain graph (for LLMGraphTransformer and Cypher queries)
    # This is separate from neo4j_driver — Neo4jGraph wraps it with LangChain integration
    from langchain_neo4j import Neo4jGraph
    app.state.neo4j_graph = Neo4jGraph(
        url = NEO4J_URI,
        username = NEO4J_USERNAME,
        password = NEO4J_PASSWORD,
        refresh_schema = False,  # 41k nodes × 35+ labels → apoc.meta.data() stalls startup 25-45s; nothing reads the cached schema
    )
    print("Neo4j LangChain graph initialized.", flush = True)
    app.state.config = {
        "configurable": {"thread_id": "1"}
    }
    # =========================================================================
    # LLM Fallback Chain — see services/llm_chain.py
    # =========================================================================
    # 13-model ordered fallback (Groq + NIM interleaved by phase:
    # large-context → 128K-quality → speed). One source of truth in
    # services/llm_chain.py so FastAPI and Celery tasks can't drift.
    # Research doc: docs/STUDY-GENERATOR-ADAPTIVE-GRADER.md (April 2026 update).
    from services.llm_chain import build_llm_fallback_chain, build_scope_classifier_llm
    app.state.llm = build_llm_fallback_chain()
    app.state.llm_scope = build_scope_classifier_llm()
    print("LLM chain loaded (see services/llm_chain.py for model order).", flush = True)
    # MinIO object storage for Knowledge Distiller artifacts (bucket self-provisions)
    from services.knowledge.storage import MinIOStudyStorage
    MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "https://minio-api.YOUR_TAILNET_DOMAIN.ts.net")
    MINIO_BUCKET = os.environ.get("MINIO_BUCKET_COELHONEXUS", "coelhonexus")
    app.state.study_storage = MinIOStudyStorage(
        bucket = MINIO_BUCKET,
        endpoint_url = MINIO_ENDPOINT,
        access_key = os.environ.get("AWS_ACCESS_KEY_ID", ""),
        secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
    )
    await app.state.study_storage.ensure_bucket()
    print(f"MinIO study storage ready: bucket={MINIO_BUCKET} at {MINIO_ENDPOINT}", flush = True)
    # PostgreSQL: auto-create database + conversation history table + checkpointer
    await _ensure_postgres_database()
    from services.youtube.conversation import ensure_conversation_table
    await ensure_conversation_table(PG_URL)
    app.state.pg_url = PG_URL
    print(f"PostgreSQL conversation history table ready.", flush = True)
    async with AsyncPostgresSaver.from_conn_string(PG_URL) as checkpointer:
        await checkpointer.setup()
        app.state.checkpointer = checkpointer
        print(f"PostgreSQL checkpointer initialized: {PG_HOST}/{PG_DATABASE}", flush = True)
        print("FastAPI startup complete.", flush = True)
        yield  # App runs here - connection stays open
        print("FastAPI shutting down...", flush = True)
        await close_transcript_service()
        print("Playwright transcript service closed.", flush = True)
        await app.state.qdrant.close()
        print("Qdrant connection closed.", flush = True)
        await app.state.neo4j_driver.close()
        print("Neo4j connection closed.", flush = True)
        await app.state.es.close()
        print("ElasticSearch connection closed.", flush = True)
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
    knowledge_distiller.router,
    prefix = "/api/v1/knowledge",
    tags = ["Knowledge"],
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
