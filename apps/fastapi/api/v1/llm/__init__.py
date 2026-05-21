"""LLM rotator router — health probe + future per-provider diagnostics."""
from fastapi import APIRouter

from .health import router as _health_router


router = APIRouter()
router.include_router(_health_router, prefix = "/health")
