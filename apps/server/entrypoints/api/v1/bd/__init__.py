"""Book Distiller feature router — aggregates the bd/ sub-routers."""
from fastapi import APIRouter

from . import ask, distill, ingestion

router = APIRouter()
router.include_router(ask.router, prefix = "/agents")
router.include_router(ingestion.router, prefix = "/ingestion")
router.include_router(distill.router, prefix = "/distill")
