"""v1 API surface. main.py mounts under /api → /api/v1/..."""
from fastapi import APIRouter

from . import bd, dd, ycs, settings

api_v1 = APIRouter(prefix = "/v1")
api_v1.include_router(settings.router, prefix = "/settings", tags = ["Settings"])
api_v1.include_router(bd.router,        prefix = "/bd",       tags = ["Book Distiller"])
api_v1.include_router(dd.router,       prefix = "/dd",       tags = ["Docs Distiller"])
api_v1.include_router(ycs.router,      prefix = "/ycs",      tags = ["YouTube Content Search"])
