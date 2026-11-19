"""Book Distiller feature router — aggregates the bd/ sub-routers."""
from . import ask, distill, ingestion, query

from fastapi import APIRouter

router = APIRouter()
router.include_router(ask.router, prefix = "/agents")
router.include_router(ingestion.router, prefix = "/ingestion")
router.include_router(distill.router, prefix = "/distill")
router.include_router(query.router, prefix = "/query")
