"""catalog router (mounted under v1/dd/catalog)."""
from . import schemas, service
from .router import router


__all__ = ["router", "schemas", "service"]
