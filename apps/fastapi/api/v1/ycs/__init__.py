"""YCS router — aggregates content, agents, admin, and query sub-routers."""
from fastapi import APIRouter

from . import admin, agents, content, query


router = APIRouter()
router.include_router(content.router, prefix = "/content")
router.include_router(agents.router,  prefix = "/agents")
router.include_router(admin.router,   prefix = "/admin")
router.include_router(query.router,   prefix = "/query")
