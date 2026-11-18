"""Docs Distiller feature router — aggregates the dd/ sub-routers. Each is mounted at its folder name, and a feature
that has sub-packages (`pages/runs`, `pipeline/planner`, `pipeline/synth`) mounts them itself, so a URL path is the
package path (guard: scripts/server/test_api_layout.py)."""

from fastapi import APIRouter

from . import catalog, debug, pages, pipeline

router = APIRouter()
router.include_router(catalog.router, prefix="/catalog")
router.include_router(pages.router, prefix="/pages")
# Existing Markdown pages may contain image links created before the API moved to /pages.
# Keep the old path as an undocumented compatibility alias while all current clients use /pages.
router.include_router(pages.router, prefix="/ingestion", include_in_schema=False)
router.include_router(debug.router, prefix="/debug")
router.include_router(pipeline.router, prefix="/pipeline")
