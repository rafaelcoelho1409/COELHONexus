"""LLM rotator router — health probe + BYOK settings (keys + selection)."""
from fastapi import APIRouter

from .health import router as _health_router
from .settings import router as _settings_router


router = APIRouter()
router.include_router(_health_router, prefix = "/health")
router.include_router(_settings_router, prefix = "/settings")
