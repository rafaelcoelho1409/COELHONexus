"""Versioned API surface (v1) — composition only, no business logic.

Domain routers are included here as each domain is ported:
  Step 2 → llm, Step 3 → docs_distiller (youtube later).

app.py mounts this under /api:  app.include_router(api_v1, prefix="/api")
→ final paths e.g. /api/v1/docs-distiller/...
"""
from fastapi import APIRouter

from .llm import router as llm_router

api_v1 = APIRouter(prefix="/v1")
api_v1.include_router(llm_router, prefix="/llm", tags=["LLM Rotator"])

# docs_distiller lands in Step 3:
# from domains.docs_distiller.routers import router as docs_distiller_router
# api_v1.include_router(docs_distiller_router, prefix="/docs-distiller", tags=["Docs Distiller"])
