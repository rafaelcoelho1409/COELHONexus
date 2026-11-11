"""Book Distiller feature router — aggregates the bd/ sub-routers."""
from fastapi import APIRouter

from . import agents, distill, ingestion, rag

router = APIRouter()
router.include_router(agents.router, prefix = "/agents")
router.include_router(ingestion.router, prefix = "/ingestion")
router.include_router(distill.router, prefix = "/distill")
router.include_router(rag.router, prefix = "/rag")
