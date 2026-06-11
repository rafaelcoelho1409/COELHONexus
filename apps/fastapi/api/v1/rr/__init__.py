"""Research Radar API surface — currently just the tool-credentials sub-router.
Future: scan trigger, scan status, SSE progress, latest digest read."""
from fastapi import APIRouter

from .tool_credentials import router as _tool_creds_router


router = APIRouter()
router.include_router(_tool_creds_router, prefix = "/tool-credentials")
