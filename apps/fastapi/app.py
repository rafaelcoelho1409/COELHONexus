"""COELHO Nexus — FastAPI base shell.

Minimal scaffold. No external dependencies wired in yet (no Redis, no MinIO,
no LLMs). Add lifespan setup + routers as features land.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers.v1.docs_distiller import router as docs_distiller_router


app = FastAPI(
    title="COELHO Nexus - FastAPI",
    description="COELHO Nexus - FastAPI",
    version="1.0.0",
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
            "frameworks": "/api/v1/docs-distiller/frameworks",
        },
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "COELHO Nexus"}
