"""Docs Distiller feature router — aggregates every endpoint module under
this package into a single APIRouter mounted by app.py."""
from fastapi import APIRouter

from .debug import router as _debug_router
from .ingestion import router as _ingestion_router
from .planner import router as _planner_router
from .resolver import router as _resolver_router
from .runs import router as _runs_router
from .synth import router as _synth_router

router = APIRouter()
router.include_router(_resolver_router, prefix="/resolver")
router.include_router(_runs_router, prefix="/runs")
router.include_router(_ingestion_router, prefix="/ingestion")
router.include_router(_debug_router, prefix="/debug")
router.include_router(_planner_router, prefix="/planner")
router.include_router(_synth_router, prefix="/synth")
