"""Research Radar API surface.

Sub-routers:
  - /tool-credentials — BYOK keys for the FastMCP source tools (step 0)
  - /scan             — POST trigger + GET status + SSE events (step 5)
  - /profile          — POST /{id}/reset-seen + future per-profile actions
"""
from fastapi import APIRouter

from .profile import router as _profile_router
from .scan import router as _scan_router
from .tool_credentials import router as _tool_creds_router


router = APIRouter()
router.include_router(_tool_creds_router, prefix = "/tool-credentials")
router.include_router(_scan_router)
router.include_router(_profile_router)
