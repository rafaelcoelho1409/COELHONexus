"""Research Radar API — /tool-credentials, /scan, /profile sub-routers."""
from fastapi import APIRouter

from .profile import router as _profile_router
from .scan import router as _scan_router
from .tool_credentials import router as _tool_creds_router


router = APIRouter()
router.include_router(_tool_creds_router, prefix = "/tool-credentials")
router.include_router(_scan_router)
router.include_router(_profile_router)
