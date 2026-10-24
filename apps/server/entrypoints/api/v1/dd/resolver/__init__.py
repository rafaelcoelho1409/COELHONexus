"""resolver router (mounted under v1/dd/resolver)."""
from . import schemas, service
from .router import router


__all__ = ["router", "schemas", "service"]
