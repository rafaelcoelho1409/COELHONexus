"""Book Distiller feature router — aggregates the bd/ sub-routers."""
from fastapi import APIRouter

from . import ingestion

router = APIRouter()
router.include_router(ingestion.router, prefix = "/ingestion")
