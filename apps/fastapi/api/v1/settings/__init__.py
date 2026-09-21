"""Settings router — external endpoint configuration (chat + embeddings)."""
from fastapi import APIRouter

from .router import router as _settings_router


router = APIRouter()
router.include_router(_settings_router, prefix = "")
