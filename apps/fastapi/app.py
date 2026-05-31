"""COELHO Nexus — FastAPI shell.

Lifespan provisions external services that Docs Distiller ingestion needs
to be functional end-to-end:

  - OTel bootstrap — Alloy gRPC + LangFuse OTLP/HTTP exporters
  - MinIO bucket — page-body storage for ingest runs
  - AsyncPostgresSaver — planner-graph checkpoint store

Add lifespan deps + routers as more features land.
"""
import logging
from contextlib import asynccontextmanager


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.v1.dd import router as dd_router
from domains.dd.ingestion.storage import get_storage
from domains.dd.planner.checkpoint import (
    close_checkpointer,
    init_checkpointer,
)
from api.v1.llm import router as llm_router
from core.otel_setup import init_otel


logger = logging.getLogger(__name__)


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

    # BYOK credential store — eager KEK init + cache load so the first rotator
    # build resolves user keys from cache (not a cold MinIO GET). Best-effort:
    # on failure the rotator falls back to env keys (today's behavior).
    try:
        from domains.llm.credentials import warm as warm_credentials
        warm_credentials()
    except Exception as e:
        logger.warning(
            f"[lifespan] LLM credential store warm failed: "
            f"{type(e).__name__}: {e}. Rotator will use env keys only."
        )

    # Build the selection-driven dynamic catalog (live discovery + benchmark
    # rank, filtered to the user's BYOK provider/model selection). Best-effort:
    # on failure the rotator falls back to the selection-filtered static catalog.
    try:
        from domains.llm.rotator.chain import init_dynamic_catalog
        await init_dynamic_catalog()
    except Exception as e:
        logger.warning(
            f"[lifespan] dynamic catalog init failed: "
            f"{type(e).__name__}: {e}. Rotator will use the static catalog."
        )

    try:
        await init_checkpointer()
    except Exception as e:
        logger.warning(
            f"[lifespan] AsyncPostgresSaver init failed: "
            f"{type(e).__name__}: {e}. Planner endpoints will 503 until "
            f"Postgres is reachable + POSTGRES_* env vars are correct."
        )

    yield

    try:
        await close_checkpointer()
    except Exception as e:
        logger.warning(f"[lifespan] checkpointer close failed: {e}")


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
    dd_router,
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
