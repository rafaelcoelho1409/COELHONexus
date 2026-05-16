"""Docs Distiller feature router — aggregates every endpoint module under
this package into a single APIRouter mounted by app.py."""
from fastapi import APIRouter

from .frameworks import router as _frameworks_router


router = APIRouter()
router.include_router(_frameworks_router)
