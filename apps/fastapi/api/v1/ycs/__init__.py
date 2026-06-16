"""YouTube Content Search feature router — aggregates the ycs/ sub-routers.

Wave 4 surface (per `docs/YCS-PORT-PLAN-2026-06-06.md`):
  content  — POST /search /videos /channel /playlist
  agents   — PUT /config + POST /search /search/stream /ingest/qdrant
             /ingest/neo4j /pipeline + GET /graph/stats
  query    — GET /namespaces + POST /{elasticsearch,qdrant,neo4j}
             (Query-page backend, see features/ycs/query/)

Wave 5 adds `admin/` — GET /admin/ingested-channels /ingested-playlists
/task/{id} (FastHTML BFF helpers)."""
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
