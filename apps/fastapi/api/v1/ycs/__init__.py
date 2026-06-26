"""YCS router — aggregates content, agents, admin, and query sub-routers."""
from fastapi import APIRouter

from .admin import router as _admin_router
from .agents import router as _agents_router
from .content import router as _content_router
from .query import router as _query_router


router = APIRouter()
router.include_router(_content_router, prefix = "/content")
router.include_router(_agents_router,  prefix = "/agents")
router.include_router(_admin_router,   prefix = "/admin")
router.include_router(_query_router,   prefix = "/query")
