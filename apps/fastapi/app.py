"""COELHO Nexus — FastAPI shell.

Lifespan provisions external services that Docs Distiller ingestion needs
to be functional end-to-end:

  - MinIO bucket — page-body storage for ingest runs. Idempotent
    ensure_bucket() — safe to call every startup; mirrors the pattern the
    deprecated app used for PostgreSQL self-provisioning.

Add lifespan deps + routers as more features land.
"""
import logging
from contextlib import asynccontextmanager


# uvicorn 0.32+ doesn't attach a handler to the root logger, so any
# `logging.getLogger(__name__).warning(...)` from app code goes nowhere
# unless we configure one. Set INFO so lifespan/init breadcrumbs are
# visible alongside uvicorn's own access log lines.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers.v1.docs_distiller import router as docs_distiller_router
from routers.v1.llm import router as llm_router
from services.docs_distiller.ingestion.storage_minio import get_storage
from services.docs_distiller.planner.checkpoint import (
    close_checkpointer,
    init_checkpointer,
)
from services.llm.otel_setup import init_otel


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- OTel bootstrap (Alloy gRPC + LangFuse OTLP/HTTP exporters) ----
    # Must run before any LLM call so the LiteLLM Router span emission has
    # an active tracer provider. Silent no-op if env vars are missing.
    try:
        init_otel(also_instrument_fastapi_app=app)
    except Exception as e:
        logger.warning(
            f"[lifespan] OTel setup failed: {type(e).__name__}: {e}. "
            f"LLM traces will not be exported."
        )

    # ---- MinIO bucket self-provisioning ----
    try:
        await get_storage().ensure_bucket()
    except Exception as e:
        # Don't crash the API for an unreachable MinIO — the resolver +
        # picker still work without it; only ingest runs would fail at
        # the first put_object. Log loudly so the failure is obvious.
        logger.warning(
            f"[lifespan] MinIO ensure_bucket failed: "
            f"{type(e).__name__}: {e}. Ingestion runs will fail until "
            f"MinIO is reachable + creds are correct."
        )

    # ---- AsyncPostgresSaver: planner-graph checkpoint store ----
    # Opens a connection pool + runs idempotent .setup() (creates
    # `checkpoints` tables). Required before any planner graph compile.
    try:
        await init_checkpointer()
    except Exception as e:
        logger.warning(
            f"[lifespan] AsyncPostgresSaver init failed: "
            f"{type(e).__name__}: {e}. Planner endpoints will 503 until "
            f"Postgres is reachable + POSTGRES_* env vars are correct."
        )

    yield

    # ---- shutdown ----
    try:
        await close_checkpointer()
    except Exception as e:
        logger.warning(f"[lifespan] checkpointer close failed: {e}")
    # aioboto3 sessions are short-lived (opened per-operation in MinIOStorage)


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

app.include_router(
    docs_distiller_router,
    prefix="/api/v1/docs-distiller",
    tags=["Docs Distiller"],
)

app.include_router(
    llm_router,
    prefix="/api/v1/llm",
    tags=["LLM Rotator"],
)


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
