"""Research Radar API — /tool-credentials, /scan, /profile sub-routers."""
from fastapi import APIRouter

from . import profile, scan, tool_credentials


router = APIRouter()
router.include_router(tool_credentials.router, prefix = "/tool-credentials")
router.include_router(scan.router)
router.include_router(profile.router)
