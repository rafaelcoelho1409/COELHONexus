"""COELHO Nexus — FastAPI shell.

Lifespan provisions: OTel (Alloy gRPC + LangFuse), MinIO bucket,
AsyncPostgresSaver, Redis, Postgres, Neo4j, ES, Qdrant, LLM chains.
"""
import api, domains, infra

import asyncio
import logging
from contextlib import asynccontextmanager

import redis.asyncio as redis_aio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        infra.otel.service.init_otel(also_instrument_fastapi_app=app)
    except Exception as e:
        logger.warning(
            f"[lifespan] OTel setup failed: {type(e).__name__}: {e}. "
            f"LLM traces will not be exported."
        )
    # Bounded (2026-09-08): ensure_bucket() previously had no timeout of its
    # own — during a MinIO hiccup it could hang the entire lifespan (and
    # thus the readiness probe) indefinitely instead of just failing the
    # try/except below. Confirmed live: a MinIO single-drive false-positive
    # "offline" event (see COELHOCloud minio module fix, same date) froze
    # this exact call and left the pod stuck at 1/2 Ready until killed.
    try:
        await asyncio.wait_for(
            domains.dd.ingestion.storage.service.get_storage().ensure_bucket(), 
            timeout=30.0)
    except asyncio.TimeoutError:
        logger.warning(
            "[lifespan] MinIO ensure_bucket timed out after 30s — "
            "continuing startup anyway. Ingestion runs will fail until "
            "MinIO is reachable + creds are correct."
        )
    except Exception as e:
        logger.warning(
            f"[lifespan] MinIO ensure_bucket failed: "
            f"{type(e).__name__}: {e}. Ingestion runs will fail until "
            f"MinIO is reachable + creds are correct."
        )

    try:
        domains.settings.credentials.service.warm()
    except Exception as e:
        logger.warning(
            f"[lifespan] LLM credential store warm failed: "
            f"{type(e).__name__}: {e}. Rotator will use env keys only."
        )

    try:
        await domains.dd.planner.runtime.checkpoint.service.init_checkpointer()
    except Exception as e:
        logger.warning(
            f"[lifespan] AsyncPostgresSaver init failed: "
            f"{type(e).__name__}: {e}. Planner endpoints will 503 until "
            f"Postgres is reachable + POSTGRES_* env vars are correct."
        )

    try:
        await domains.rr.service.bootstrap_stores()
    except Exception as e:
        logger.warning(
            f"[lifespan] RR bootstrap_stores failed: "
            f"{type(e).__name__}: {e}. Radar /v1/rr/scan endpoints will 5xx "
            f"until the missing store is reachable."
        )

    try:
        await infra.elasticsearch.service.ensure_indexes()
    except Exception as e:
        logger.warning(
            f"[lifespan] Elasticsearch ensure_indexes failed: "
            f"{type(e).__name__}: {e}. YCS endpoints will 503 until ES is "
            f"reachable + ELASTICSEARCH_* env vars are correct."
        )

    try:
        infra.neo4j.service.get_graph()
        await infra.neo4j.service.verify_connectivity()
    except Exception as e:
        logger.warning(
            f"[lifespan] Neo4j connectivity failed: {type(e).__name__}: {e}. "
            f"YCS graph endpoints will 503 until Neo4j is reachable + "
            f"NEO4J_* env vars are correct."
        )

    try:
        app.state.redis_aio = redis_aio.from_url(
            domains.ycs.runtime.keys.redis_url(),
        )
    except Exception as e:
        app.state.redis_aio = None
        logger.warning(
            f"[lifespan] YCS Redis async client init failed: "
            f"{type(e).__name__}: {e}. YCS cache + agents /config will 5xx."
        )

    try:
        app.state.pg_url = domains.ycs.runtime.keys.postgres_url()
        await domains.ycs.conversation.service.ensure_conversation_table(
            app.state.pg_url)
    except Exception as e:
        logger.warning(
            f"[lifespan] YCS conversation table init failed: "
            f"{type(e).__name__}: {e}. YCS thread memory will 5xx."
        )

    try:
        app.state.neo4j_graph = infra.neo4j.service.get_graph()
    except Exception as e:
        app.state.neo4j_graph = None
        logger.warning(
            f"[lifespan] YCS Neo4jGraph init failed: "
            f"{type(e).__name__}: {e}. /agents/graph/stats will 5xx."
        )

    try:
        app.state.llm = api.v1.ycs.agents.service.build_deprecated_llm_chain()
    except Exception as e:
        app.state.llm = None
        logger.warning(
            f"[lifespan] YCS LLM chain init failed: "
            f"{type(e).__name__}: {e}. /agents/search will 5xx."
        )

    # Dedicated FAST-mode client (short outputs, own bandit cell) —
    # direct_answer prefers it, falls back to app.state.llm when None.
    try:
        app.state.llm_fast = api.v1.ycs.agents.service.build_fast_llm_chain()
    except Exception as e:
        app.state.llm_fast = None
        logger.warning(
            f"[lifespan] YCS FAST LLM chain init failed: "
            f"{type(e).__name__}: {e}. FAST falls back to app.state.llm."
        )

    # query_ai_llm targets the external provider for NL→DSL translation
    # (tiny deterministic output — 40s ceiling is plenty); falls back
    # to app.state.llm at request time.
    # 2026-09-17: 120s → 60s. `ai_generate_stream`'s `_stream_with_retry`
    # now retries once on a dead endpoint pick (live-observed: the
    # endpoint hanging the full budget with zero tokens back) — at
    # 120s/attempt that made the worst case ~240s before the user saw
    # anything. 60s keeps 2 attempts inside the old single-attempt
    # ceiling; a healthy arm's NL→DSL output is well under 10s anyway.
    # 2026-09-24: 60s → 40s, paired with `max_attempts` going 2→3 in
    # the same commit — live-reproduced the 2026-09-17 scenario again
    # (two consecutive hung arms), which needed a 3rd attempt to
    # recover. Kept at 3×40s=120s instead of letting 3×60s=180s become
    # the new worst case, since a healthy arm is still well under 10s.
    try:
        app.state.query_ai_llm = domains.settings.chat.service.build_chat_model(
            timeout_s=40.0,
        )
    except Exception as e:
        app.state.query_ai_llm = None
        logger.warning(
            f"[lifespan] YCS Query AI fast-chain init failed: "
            f"{type(e).__name__}: {e}. /ycs/query/ai/* will fall back to "
            f"the slower dd-all chain."
        )

    try:
        es = infra.elasticsearch.service.get_es()
        qdrant = infra.qdrant.service.get_qdrant()
        es_retriever = domains.ycs.retriever.service.ElasticsearchRetriever(es)
        qdrant_retriever = domains.ycs.retriever.service.QdrantHybridRetriever(
            qdrant            = qdrant,
            dense_embeddings  = domains.ycs.embeddings.service.create_dense_embeddings(),
            sparse_embeddings = domains.ycs.embeddings.service.create_sparse_embeddings(),
            es_client         = es,
        )
        neo4j_retriever = (
            domains.ycs.retriever.service.Neo4jRetriever(
                neo4j_graph = app.state.neo4j_graph,
                llm         = app.state.llm,
            )
            if app.state.neo4j_graph is not None and app.state.llm is not None
            else None
        )
        app.state.smart_retriever = domains.ycs.retriever.service.SmartRetriever(
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
            domains.ycs.grader.service.DocumentGrader(app.state.llm)
            if app.state.llm is not None else None
        )
    except Exception as e:
        app.state.grader = None
        logger.warning(
            f"[lifespan] YCS grader init failed: "
            f"{type(e).__name__}: {e}. /agents/search will 5xx."
        )

    yield

    try:
        await domains.dd.planner.runtime.checkpoint.service.close_checkpointer()
    except Exception as e:
        logger.warning(f"[lifespan] checkpointer close failed: {e}")

    try:
        await infra.elasticsearch.service.close_es()
    except Exception as e:
        logger.warning(f"[lifespan] elasticsearch close failed: {e}")

    try:
        await infra.neo4j.service.close_neo4j()
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

app.include_router(api.v1.router.api_v1, prefix="/api")


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
