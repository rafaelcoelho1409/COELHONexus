"""COELHO Nexus — FastAPI shell.

Lifespan provisions: OTel (Alloy gRPC + LangFuse), MinIO bucket,
AsyncPostgresSaver, Redis, Postgres, Neo4j, ES, Qdrant, LLM chains.
"""
import logging
import os
from contextlib import asynccontextmanager


_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "[trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] %(message)s"
)


def _install_log_record_defaults() -> None:
    old_factory = logging.getLogRecordFactory()
    if getattr(old_factory, "_coelho_otel_defaults", False):
        return

    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.otelTraceID = getattr(record, "otelTraceID", "0")
        record.otelSpanID = getattr(record, "otelSpanID", "0")
        return record

    record_factory._coelho_otel_defaults = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(record_factory)


# basicConfig BEFORE first-party imports so module-load log calls
# (e.g. domains.llm.rotator.chain registers LiteLLM's OTel callback at
# import time and logs about it) use our format, not stderr default.
_install_log_record_defaults()
logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FORMAT,
)

import redis.asyncio as redis_aio_module
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.v1.router import api_v1
from api.v1.ycs.agents.llm_chain import build_deprecated_llm_chain
from domains.dd.ingestion.storage import get_storage
from domains.dd.planner.runtime.checkpoint import (
    close_checkpointer,
    init_checkpointer,
)
from domains.llm.credentials import warm as warm_credentials
from domains.llm.rotator.chain import (
    build_reduce_label_chain,
    init_dynamic_catalog,
    start_catalog_refresh_loop,
    stop_catalog_refresh_loop,
)
from domains.rr.service import bootstrap_stores as bootstrap_rr_stores
from domains.ycs.conversation import ensure_conversation_table
from domains.ycs.embeddings import (
    create_dense_embeddings,
    create_sparse_embeddings,
)
from domains.ycs.grader import DocumentGrader
from domains.ycs.retriever import (
    ElasticsearchRetriever,
    Neo4jRetriever,
    QdrantHybridRetriever,
    SmartRetriever,
)
from infra.elasticsearch import (
    close_es,
    ensure_indexes as ensure_es_indexes,
    get_es,
)
from infra.neo4j import (
    close_neo4j,
    get_graph as get_neo4j_graph,
    verify_connectivity as verify_neo4j_connectivity,
)
from infra.otel import init_otel
from infra.qdrant import get_qdrant


logger = logging.getLogger(__name__)


def _redis_url_from_env() -> str:
    # URL-encode password so DSN parsing survives %, @, &, etc.
    from urllib.parse import quote
    host = os.environ.get("REDIS_HOST", "localhost")
    port = os.environ.get("REDIS_PORT", "6379")
    password = os.environ.get("REDIS_PASSWORD", "")
    if password:
        return f"redis://:{quote(password, safe = '')}@{host}:{port}"
    return f"redis://{host}:{port}"


def _postgres_url_from_env() -> str:
    # URL-encode user+password; raw % in a password breaks asyncpg DSN parsing with "invalid percent-encoded token".
    from urllib.parse import quote
    user = quote(os.environ.get("POSTGRES_USER", "postgres"), safe = "")
    password = quote(os.environ.get("POSTGRES_PASSWORD", ""), safe = "")
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "postgres")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_otel(also_instrument_fastapi_app=app)
    except Exception as e:
        logger.warning(
            f"[lifespan] OTel setup failed: {type(e).__name__}: {e}. "
            f"LLM traces will not be exported."
        )

    try:
        await get_storage().ensure_bucket()
    except Exception as e:
        logger.warning(
            f"[lifespan] MinIO ensure_bucket failed: "
            f"{type(e).__name__}: {e}. Ingestion runs will fail until "
            f"MinIO is reachable + creds are correct."
        )

    try:
        warm_credentials()
    except Exception as e:
        logger.warning(
            f"[lifespan] LLM credential store warm failed: "
            f"{type(e).__name__}: {e}. Rotator will use env keys only."
        )

    try:
        await init_dynamic_catalog()
    except Exception as e:
        logger.warning(
            f"[lifespan] dynamic catalog init failed: "
            f"{type(e).__name__}: {e}. Rotator will use the static catalog."
        )

    # Periodic re-discovery drops EOL'd models without waiting for a redeploy.
    try:
        start_catalog_refresh_loop()
    except Exception as e:
        logger.warning(
            f"[lifespan] catalog refresh loop start failed: "
            f"{type(e).__name__}: {e}. EOL'd models will only drop on redeploy."
        )

    try:
        await init_checkpointer()
    except Exception as e:
        logger.warning(
            f"[lifespan] AsyncPostgresSaver init failed: "
            f"{type(e).__name__}: {e}. Planner endpoints will 503 until "
            f"Postgres is reachable + POSTGRES_* env vars are correct."
        )

    try:
        await bootstrap_rr_stores()
    except Exception as e:
        logger.warning(
            f"[lifespan] RR bootstrap_stores failed: "
            f"{type(e).__name__}: {e}. Radar /v1/rr/scan endpoints will 5xx "
            f"until the missing store is reachable."
        )

    try:
        await ensure_es_indexes()
    except Exception as e:
        logger.warning(
            f"[lifespan] Elasticsearch ensure_indexes failed: "
            f"{type(e).__name__}: {e}. YCS endpoints will 503 until ES is "
            f"reachable + ELASTICSEARCH_* env vars are correct."
        )

    try:
        get_neo4j_graph()
        await verify_neo4j_connectivity()
    except Exception as e:
        logger.warning(
            f"[lifespan] Neo4j connectivity failed: {type(e).__name__}: {e}. "
            f"YCS graph endpoints will 503 until Neo4j is reachable + "
            f"NEO4J_* env vars are correct."
        )

    try:
        app.state.redis_aio = redis_aio_module.from_url(
            _redis_url_from_env(),
        )
    except Exception as e:
        app.state.redis_aio = None
        logger.warning(
            f"[lifespan] YCS Redis async client init failed: "
            f"{type(e).__name__}: {e}. YCS cache + agents /config will 5xx."
        )

    try:
        app.state.pg_url = _postgres_url_from_env()
        await ensure_conversation_table(app.state.pg_url)
    except Exception as e:
        logger.warning(
            f"[lifespan] YCS conversation table init failed: "
            f"{type(e).__name__}: {e}. YCS thread memory will 5xx."
        )

    try:
        app.state.neo4j_graph = get_neo4j_graph()
    except Exception as e:
        app.state.neo4j_graph = None
        logger.warning(
            f"[lifespan] YCS Neo4jGraph init failed: "
            f"{type(e).__name__}: {e}. /agents/graph/stats will 5xx."
        )

    try:
        app.state.llm = build_deprecated_llm_chain()
    except Exception as e:
        app.state.llm = None
        logger.warning(
            f"[lifespan] YCS LLM chain init failed: "
            f"{type(e).__name__}: {e}. /agents/search will 5xx."
        )

    # query_ai_llm uses the dd-reduce-label pool (no reasoning models) to avoid 1-15s think tokens
    # on NL→DSL translation; falls back to app.state.llm at request time.
    try:
        app.state.query_ai_llm = build_reduce_label_chain()
    except Exception as e:
        app.state.query_ai_llm = None
        logger.warning(
            f"[lifespan] YCS Query AI fast-chain init failed: "
            f"{type(e).__name__}: {e}. /ycs/query/ai/* will fall back to "
            f"the slower dd-all chain."
        )

    try:
        es = get_es()
        qdrant = get_qdrant()
        es_retriever = ElasticsearchRetriever(es)
        qdrant_retriever = QdrantHybridRetriever(
            qdrant            = qdrant,
            dense_embeddings  = create_dense_embeddings(),
            sparse_embeddings = create_sparse_embeddings(),
        )
        neo4j_retriever = (
            Neo4jRetriever(
                neo4j_graph = app.state.neo4j_graph,
                llm         = app.state.llm,
            )
            if app.state.neo4j_graph is not None and app.state.llm is not None
            else None
        )
        app.state.smart_retriever = SmartRetriever(
            es_retriever      = es_retriever,
            qdrant_retriever  = qdrant_retriever,
            neo4j_retriever   = neo4j_retriever,
        )
    except Exception as e:
        app.state.smart_retriever = None
        logger.warning(
            f"[lifespan] YCS smart retriever init failed: "
            f"{type(e).__name__}: {e}. /agents/search will 5xx."
        )

    try:
        app.state.grader = (
            DocumentGrader(app.state.llm) if app.state.llm is not None else None
        )
    except Exception as e:
        app.state.grader = None
        logger.warning(
            f"[lifespan] YCS grader init failed: "
            f"{type(e).__name__}: {e}. /agents/search will 5xx."
        )

    yield

    try:
        await stop_catalog_refresh_loop()
    except Exception as e:
        logger.warning(f"[lifespan] catalog refresh loop stop failed: {e}")

    try:
        await close_checkpointer()
    except Exception as e:
        logger.warning(f"[lifespan] checkpointer close failed: {e}")

    try:
        await close_es()
    except Exception as e:
        logger.warning(f"[lifespan] elasticsearch close failed: {e}")

    try:
        await close_neo4j()
    except Exception as e:
        logger.warning(f"[lifespan] neo4j close failed: {e}")

    try:
        if getattr(app.state, "redis_aio", None) is not None:
            await app.state.redis_aio.close()
    except Exception as e:
        logger.warning(f"[lifespan] YCS redis close failed: {e}")


app = FastAPI(
    title="COELHO Nexus - FastAPI",
    description="COELHO Nexus - FastAPI",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1, prefix="/api")


@app.get("/")
async def root():
    return {
        "service": "FastAPI Service - COELHO Nexus",
        "version": "1.0.0",
        "endpoints": {
            "docs": "/docs",
            "health": "/health",
            "resolver": "/api/v1/docs-distiller/resolver",
            "runs": "/api/v1/docs-distiller/runs",
        },
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "COELHO Nexus"}
