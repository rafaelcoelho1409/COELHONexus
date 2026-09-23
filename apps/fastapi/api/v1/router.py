"""v1 API surface. app.py mounts under /api → /api/v1/..."""
from fastapi import APIRouter

from . import dd, rr, ycs, settings

api_v1 = APIRouter(prefix = "/v1")
api_v1.include_router(settings.router, prefix = "/settings", tags = ["Settings"])
api_v1.include_router(dd.router, prefix = "/docs-distiller", tags = ["Docs Distiller"])
api_v1.include_router(ycs.router, prefix = "/ycs", tags = ["YouTube Content Search"])
api_v1.include_router(rr.router, prefix = "/rr", tags = ["Research Radar"])
