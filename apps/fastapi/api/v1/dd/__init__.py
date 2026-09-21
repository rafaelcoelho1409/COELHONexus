"""Docs Distiller feature router — aggregates the dd/ sub-routers."""
from fastapi import APIRouter

from . import debug, ingestion, pipeline, planner, resolver, runs, synth

router = APIRouter()
router.include_router(resolver.router, prefix="/resolver")
router.include_router(runs.router, prefix="/runs")
router.include_router(ingestion.router, prefix="/ingestion")
router.include_router(debug.router, prefix="/debug")
router.include_router(planner.router, prefix="/planner")
router.include_router(synth.router, prefix="/synth")
router.include_router(pipeline.router, prefix="/pipeline")
