"""LLM rotator router — health probe + BYOK settings (keys + selection) + OpenAI compat."""
from fastapi import APIRouter

from .health import router as _health_router
from .openai import router as _openai_router
from .settings import router as _settings_router


router = APIRouter()
router.include_router(_health_router, prefix = "/health")
router.include_router(_settings_router, prefix = "/settings")
router.include_router(_openai_router, prefix = "/openai")
