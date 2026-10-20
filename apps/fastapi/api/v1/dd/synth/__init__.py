"""synth router (mounted under v1/dd/synth)."""
from . import service
from .router import router


__all__ = ["router", "service"]
