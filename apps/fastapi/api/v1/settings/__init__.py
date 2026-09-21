"""Settings router — external endpoint configuration (chat + embeddings)."""
from . import schemas
from .router import router


__all__ = ["router", "schemas"]
