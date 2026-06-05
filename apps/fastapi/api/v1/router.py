"""v1 API surface. app.py mounts under /api → /api/v1/..."""
from fastapi import APIRouter

from .dd import router as dd_router
from .llm import router as llm_router

api_v1 = APIRouter(prefix = "/v1")
api_v1.include_router(llm_router, prefix = "/llm", tags = ["LLM Rotator"])
api_v1.include_router(dd_router, prefix = "/docs-distiller", tags = ["Docs Distiller"])
