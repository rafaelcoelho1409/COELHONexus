"""Docs Distiller feature router — aggregates the dd/ sub-routers. Each is mounted at its folder name, and a feature
that has sub-packages (`ingestion/runs`, `pipeline/planner`, `pipeline/synth`) mounts them itself, so a URL path is the
package path (guard: scripts/server/test_api_layout.py)."""
from fastapi import APIRouter

from . import catalog, debug, ingestion, pipeline

router = APIRouter()
router.include_router(catalog.router,   prefix="/catalog")
router.include_router(ingestion.router, prefix="/ingestion")
router.include_router(debug.router,     prefix="/debug")
router.include_router(pipeline.router,  prefix="/pipeline")
