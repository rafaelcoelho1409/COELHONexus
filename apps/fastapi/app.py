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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers.v1.docs_distiller import router as docs_distiller_router
from services.docs_distiller.ingestion.storage_minio import get_storage


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    yield
    # Nothing to tear down explicitly — aioboto3 sessions are short-lived
    # (opened per-operation in storage_minio.MinIOStorage).


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
